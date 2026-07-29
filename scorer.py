"""array-aware scorer for document-extraction results vs hand-labeled ground truth.

self-contained, stdlib only. scores a single (result, schema, label) triple and returns a
dict with per-array and non-array sub-scores plus a "final" float in [0, 1].

scoring model:
  - array fields are scored with per-item greedy matching (order-independent), weighted by leaf count
  - non-array fields are scored with a binary 0/1 per leaf node
  - the final score is a leaf-weighted average across both
"""
import json
import re
import unicodedata
from typing import Any


# --- normalization helpers ---

def cast_numbers_to_float(data):
    """recursively cast all numeric values to floats, rounded to 6 decimals
    to absorb fp-tail noise from computed sums (e.g. 8.459999999999999 -> 8.46).
    """
    if isinstance(data, dict):
        return {k: cast_numbers_to_float(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [cast_numbers_to_float(elem) for elem in data]
    elif isinstance(data, (int, float)) and not isinstance(data, bool):
        return round(float(data), 6)
    return data


def normalize(val: str) -> str:
    """remove all whitespace, punctuation, and lowercase.
    """
    # fold unicode compatibility variants first so cjk fullwidth forms compare equal to ascii (１１１ == 111)
    val = unicodedata.normalize("NFKC", val)
    val = re.sub(r"\s+", "", val).lower()
    val = re.sub(r"\W", "", val, flags=re.UNICODE)
    return val


def normalize_strings(data):
    """recursively apply normalize() to all string values.
    """
    if isinstance(data, dict):
        return {k: normalize_strings(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [normalize_strings(item) for item in data]
    elif isinstance(data, str):
        return normalize(data)
    return data


def is_empty(val):
    """check if a value is empty (None, empty string, empty list, empty dict).
    """
    return val is None or val == "" or val == [] or val == {}


def strip_empty_values(data):
    """recursively remove keys with empty string, empty list, or empty dict values.
    None is preserved — it represents an intentional null in the label.
    """
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            v = strip_empty_values(v)
            if v != "" and v != [] and v != {}:
                cleaned[k] = v
        return cleaned
    elif isinstance(data, list):
        return [strip_empty_values(item) for item in data]
    return data


# --- schema traversal ---

def extract_schema_arrays_with_parent(x, parents=None):
    """extract all array fields from the schema.
    every time we encounter an array, we add it to the list.
    note: we do not run recursively within an array to find more arrays.
    """
    schema_arrays = []
    parents = [] if parents is None else parents
    if isinstance(x, dict):
        if "type" in x and ((isinstance(x["type"], list) and "array" in x["type"]) or x["type"] == "array"):
            schema_arrays.append({"object": x, "path": parents})
            return schema_arrays
        for key, value in x.items():
            schema_arrays.extend(extract_schema_arrays_with_parent(value, parents + [key]))
    elif isinstance(x, list):
        for item in x:
            schema_arrays.extend(extract_schema_arrays_with_parent(item, parents))
    return schema_arrays


def extract_schema_arrays(schema):
    """extract all array fields from the schema, including their parent structure.
    """
    schema_arrays = extract_schema_arrays_with_parent(schema)
    output = []
    for schema_array in schema_arrays:
        full_schema = {}
        if "$schema" in schema:
            full_schema["$schema"] = schema["$schema"]
        if "description" in schema:
            full_schema["description"] = schema["description"]
        full_schema["type"] = "object"
        sub_schema = full_schema
        for i, path in enumerate(schema_array["path"]):
            if i == len(schema_array["path"]) - 1:
                sub_schema[path] = schema_array["object"]
            elif path == "properties":
                sub_schema[path] = {}
            else:
                sub_schema[path] = {"type": "object"}
            sub_schema = sub_schema[path]
        output.append(full_schema)
    return output


def extract_schema_without_arrays(x):
    """remove all array fields from the schema.
    this complements extract_schema_arrays, leaving only the non-array fields of the schema.
    """
    if isinstance(x, dict):
        # remove this field entirely if it's an array type
        if "type" in x and ((isinstance(x["type"], list) and "array" in x["type"]) or x["type"] == "array"):
            return {}
        else:
            # recursively process dict entries
            out = {}
            for k, v in x.items():
                processed_v = extract_schema_without_arrays(v)
                if processed_v not in ({}, [], None):
                    out[k] = processed_v

            # if this dict is an object with no non-array fields, remove it
            if 'type' in out:
                types = out['type'] if isinstance(out['type'], list) else [out['type']]
                if 'object' in types:
                    if 'properties' not in out or not out['properties']:
                        return {}
            return out

    # recursively process list items and filter out empty results
    elif isinstance(x, list):
        processed_list = [extract_schema_without_arrays(item) for item in x]
        processed_list = [item for item in processed_list if item not in ({}, [], None)]
        return processed_list if processed_list else {}
    else:
        return x  # noqa


def get_primary_schema_array_path(schema):
    """a primary array schema has just a single path down, ending in an array object.
    """
    path = []
    current_schema = schema
    while True:
        if "type" not in current_schema:
            raise ValueError("Schema must have a type field")
        types = current_schema["type"] if isinstance(current_schema["type"], list) else [current_schema["type"]]
        if "object" in types:
            assert "properties" in current_schema, "Schema must have a properties field"
            schema_keys = list(current_schema["properties"].keys())
            assert len(schema_keys) == 1, "Schema must have a single root level array field"
            path.append(schema_keys[0])
            current_schema = current_schema["properties"][schema_keys[0]]
        elif "array" in types:
            assert "items" in current_schema, "Schema must have an items field"
            break
        else:
            raise ValueError("Schema must have an object or array type")
    return path


def get_array_name(p):
    while True:
        if "type" in p and "properties" in p:
            p = p["properties"]
        elif len(p) == 1:
            return list(p.keys())[0]
        else:
            return None


def extract_array_items(data: Any, array_schema: dict) -> list:
    """extract the array items list from data by following the schema path.
    array_schema is a single-path schema returned by extract_schema_arrays (one path down to an array).
    """
    path = get_primary_schema_array_path(array_schema)
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return []
        current = current[key]
    if not isinstance(current, list):
        return []
    return current


# --- comparison logic ---

def flatten_to_leaves(obj: Any, prefix: str = "") -> dict:
    """flatten a nested dict/value to leaf-level key-value pairs for field-by-field comparison.
    nested arrays are stringified for comparison. primitive values are returned as-is.
    """
    if isinstance(obj, dict):
        leaves: dict = {}
        for k, v in obj.items():
            new_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                leaves.update(flatten_to_leaves(v, new_key))
            elif isinstance(v, list):
                leaves[new_key] = json.dumps(v, sort_keys=True)
            else:
                leaves[new_key] = v
        return leaves
    # primitive item (for arrays of primitives)
    return {prefix or "_val": obj}


def compare_items(label_item: Any, result_item: Any) -> float:
    """compare two array items by binary field matching on leaf nodes.
    returns fraction of matching fields (0 to 1).
    scores all fields present in either item. both-empty = skip, one-empty = mismatch.
    """
    label_leaves = flatten_to_leaves(label_item)
    result_leaves = flatten_to_leaves(result_item)

    # score all fields present in either the label or the result
    all_keys = set(label_leaves.keys()) | set(result_leaves.keys())
    scored_keys = [k for k in all_keys if not (is_empty(label_leaves.get(k)) and is_empty(result_leaves.get(k)))]
    if not scored_keys:
        return 1.0

    matches = 0
    for key in scored_keys:
        lv = label_leaves.get(key)
        rv = result_leaves.get(key)
        if lv == rv:
            matches += 1
    return matches / len(scored_keys)


def avg_label_leaf_count(label_items: list) -> float:
    """compute the average number of non-empty leaf fields across label items.
    used to weight array items by field count so each leaf node gets equal weight.
    """
    if not label_items:
        return 1.0
    counts = []
    for item in label_items:
        leaves = flatten_to_leaves(item)
        count = sum(1 for v in leaves.values() if not is_empty(v))
        counts.append(max(count, 1))
    return sum(counts) / len(counts)


def score_array_per_item(label_items: list, result_items: list):
    """per-item matching scorer for arrays.
    builds a similarity matrix, does greedy best-pair assignment, unmatched items score 0.
    returns (score, reorder) where reorder is the result indices sorted to align with label.
    """
    if not label_items and not result_items:
        return 1.0, []
    n_label = len(label_items)
    n_result = len(result_items)
    if n_label == 0 or n_result == 0:
        return 0.0, list(range(n_result))

    # build similarity pairs
    pairs = []
    for i, li in enumerate(label_items):
        for j, rj in enumerate(result_items):
            pairs.append((compare_items(label_item=li, result_item=rj), i, j))
    pairs.sort(key=lambda x: x[0], reverse=True)

    # greedy assignment: pick best-scoring pair, remove both, repeat
    total_score = 0.0
    used_labels: set = set()
    used_results: set = set()
    label_to_result: dict = {}
    for score, i, j in pairs:
        if i in used_labels or j in used_results:
            continue
        total_score += score
        used_labels.add(i)
        used_results.add(j)
        label_to_result[i] = j

    # build reorder: matched result indices in label order, then unmatched
    reorder = [label_to_result[i] for i in range(n_label) if i in label_to_result]
    reorder += [j for j in range(n_result) if j not in used_results]
    return total_score / max(n_label, n_result), reorder


def get_binary_score(result, label, schema):
    """binary 0/1 score for each leaf node in non-array schema fields.
    """

    def _recursive_score(val1, val2, schema_part):
        if schema_part.get("type") == "object" and "properties" in schema_part:
            correct_count, total_count = 0, 0
            for prop_name, prop_schema in schema_part["properties"].items():
                sub_correct, sub_total = _recursive_score(
                    val1.get(prop_name) if isinstance(val1, dict) else None,
                    val2.get(prop_name) if isinstance(val2, dict) else None,
                    prop_schema)
                correct_count += sub_correct
                total_count += sub_total
            return correct_count, total_count
        else:
            if is_empty(val1) and is_empty(val2):
                return 0, 0
            elif val1 == val2:
                return 1, 1
            else:
                return 0, 1

    correct, total = _recursive_score(result, label, schema)
    score = correct / total if total else 1.0
    return {"score": score, "total": total}


def score_standardization(result: dict, schema: dict, label: dict) -> dict:
    """score an extraction result against a label using per-item matching for arrays.
    returns a dict with per-array sub-scores, a non_array sub-score, and a "final" float.
    """
    output_dict = {}
    result_norm = normalize_strings(cast_numbers_to_float(strip_empty_values(result)))
    label_norm = normalize_strings(cast_numbers_to_float(strip_empty_values(label)))

    schema_arrays = extract_schema_arrays(schema=schema)
    if len(schema_arrays) > 0:
        output_dict["arrays"] = {}
        for schema_array in schema_arrays:
            array_name = get_array_name(schema_array)
            label_items = extract_array_items(data=label_norm, array_schema=schema_array)
            result_items = extract_array_items(data=result_norm, array_schema=schema_array)
            max_num_items = max(len(label_items), len(result_items))
            score, _ = score_array_per_item(label_items=label_items, result_items=result_items)

            # weight by leaf field count so each leaf node gets equal weight
            fields_per_item = avg_label_leaf_count(label_items)
            output_dict["arrays"][array_name] = {"score": score, "total": max_num_items * fields_per_item}

    schema_non_array = extract_schema_without_arrays(schema)
    if len(schema_non_array.get("properties", {})) > 0:
        output_dict["non_array"] = get_binary_score(result=result_norm, label=label_norm, schema=schema_non_array)

    # final score: weighted average where each leaf field has equal weight
    if "arrays" not in output_dict:
        output_dict["final"] = output_dict["non_array"]["score"]
    else:
        total_weight = 0
        weighted_score = 0
        for array in output_dict["arrays"].values():
            total_weight += array["total"]
            weighted_score += array["score"] * array["total"]
        if "non_array" in output_dict:
            total_weight += output_dict["non_array"]["total"]
            weighted_score += output_dict["non_array"]["score"] * output_dict["non_array"]["total"]
        output_dict["final"] = weighted_score / total_weight if total_weight > 0 else 1.0
    return output_dict


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        print("usage: python scorer.py <result.json> <schema.json> <label.json>")
        sys.exit(1)

    result = json.load(open(sys.argv[1]))
    schema = json.load(open(sys.argv[2]))
    label = json.load(open(sys.argv[3]))
    out = score_standardization(result=result, schema=schema, label=label)
    print(json.dumps(out, indent=2))
