from pathlib import Path

PATH = Path(__file__).with_name("probe_sections.py")

INSERT_AFTER = '''def classify_current(nodes, client_rect, screenshot_path=None, window_rect=None):\n    \"\"\"Run the live classifier using both UIA and captured pixels.\"\"\"\n    visible = [\n        node for node in nodes\n        if wkcommon.node_is_visibly_present(node, client_rect)[0]\n    ]\n    parents = ctx.build_parent_map(nodes)\n    node_index = {id(node): i for i, node in enumerate(nodes)}\n    visible_heads = ctx._visible_heading_nodes(visible)\n    heading_doc_ids = {}\n    for english, node in visible_heads.items():\n        idx = node_index.get(id(node))\n        heading_doc_ids[english] = (\n            ctx.document_index_of(idx, nodes, parents)\n            if idx is not None else None)\n    return ctx.classify_live_page(\n        visible, heading_doc_ids, screenshot_path, window_rect)\n'''

HELPER = '''\n\ndef active_rating_document_nodes(nodes, client_rect, screenshot_path, window_rect):\n    \"\"\"Return only the currently rendered rating-form Document subtree.\n\n    WeChat/Chromium keeps stale Page-Frames alive.  The page classifier already\n    resolves duplicate headings against the screenshot; reuse that same visual\n    selection here and keep only the Document that owns the rendered\n    ``Making Skills`` heading.  This prevents roster/calendar nodes from being\n    mistaken for score rows.\n    \"\"\"\n    visible = [\n        node for node in nodes\n        if wkcommon.node_is_visibly_present(node, client_rect)[0]\n    ]\n    selected, support = ctx._select_visual_heading_nodes(\n        visible, screenshot_path, window_rect)\n    anchor = selected.get(\"Making Skills\")\n    if not anchor or not support.get(\"Making Skills\", {}).get(\"supported\"):\n        return [], \"visually verified Making Skills heading not found\"\n\n    index_by_id = {id(node): i for i, node in enumerate(nodes)}\n    anchor_index = index_by_id.get(id(anchor))\n    if anchor_index is None:\n        return [], \"rendered heading not found in UIA snapshot\"\n    parents = ctx.build_parent_map(nodes)\n    doc_index = ctx.document_index_of(anchor_index, nodes, parents)\n    if doc_index is None:\n        return [], \"rendered heading has no Document ancestor\"\n\n    doc_depth = nodes[doc_index].get(\"depth\", 0)\n    subtree = [nodes[doc_index]]\n    for node in nodes[doc_index + 1:]:\n        if node.get(\"depth\", 0) <= doc_depth:\n            break\n        subtree.append(node)\n    return subtree, \"Document index {} ({} nodes)\".format(doc_index, len(subtree))\n'''

OLD_PRE = '''    mapping = wkcommon.map_rating_sections(nodes)\n    sections = mapping[\"sections\"]\n'''
NEW_PRE = '''    active_nodes, active_reason = active_rating_document_nodes(\n        nodes, client_rect, precheck_shot, window_rect)\n    emit(\"active rating document: {}\".format(active_reason))\n    if not active_nodes:\n        emit(\"ABORT: could not isolate the rendered rating document. Nothing was clicked.\")\n        with open(report_path, \"w\", encoding=\"utf-8\") as fh:\n            fh.write(\"\\n\".join(out) + \"\\n\")\n        print(\"Active rating document not identified - nothing clicked.\")\n        print(\"REPORT: {}\".format(report_path))\n        return 3\n    nodes = active_nodes\n    mapping = wkcommon.map_rating_sections(nodes)\n    sections = mapping[\"sections\"]\n'''

OLD_POST = '''            mapped_after = wkcommon.map_rating_sections(nodes)\n            if not mapped_after[\"sections\"] or not mapped_after[\"order_ok\"]:\n'''
NEW_POST = '''            active_nodes, active_reason = active_rating_document_nodes(\n                nodes, client_rect, postcheck_shot, window_rect)\n            emit(\"  active rating document after click: {}\".format(active_reason))\n            if not active_nodes:\n                entry[\"state_after\"] = \"MAPPING-FAILED\"\n                entry[\"aborted\"] = \"rendered rating document could not be isolated after expansion\"\n                emit(\"  ABORT: {}; stopping.\".format(entry[\"aborted\"]))\n                findings.append(entry)\n                break\n            nodes = active_nodes\n            mapped_after = wkcommon.map_rating_sections(nodes)\n            if not mapped_after[\"sections\"] or not mapped_after[\"order_ok\"]:\n'''

OLD_STATE = '''        shot_after = os.path.join(shots, \"{}_{}_after.png\".format(stamp, index + 1))\n'''
NEW_STATE = '''        entry[\"state_after\"] = after[\"state\"]\n        shot_after = os.path.join(shots, \"{}_{}_after.png\".format(stamp, index + 1))\n'''


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f\"{label}: expected exactly one match, found {count}\")
    return text.replace(old, new, 1)


def main():
    text = PATH.read_text(encoding=\"utf-8\")
    if \"def active_rating_document_nodes\" not in text:
        text = replace_once(text, INSERT_AFTER, INSERT_AFTER + HELPER, \"helper insertion\")
    if \"active rating document: {}\" not in text:
        text = replace_once(text, OLD_PRE, NEW_PRE, \"precheck filtering\")
    if \"active rating document after click\" not in text:
        text = replace_once(text, OLD_POST, NEW_POST, \"postcheck filtering\")
    if 'entry[\"state_after\"] = after[\"state\"]' not in text:
        text = replace_once(text, OLD_STATE, NEW_STATE, \"state summary fix\")
    PATH.write_text(text, encoding=\"utf-8\")
    print(\"probe_sections.py patched: active Document isolation + summary state fix\")


if __name__ == \"__main__\":
    main()
