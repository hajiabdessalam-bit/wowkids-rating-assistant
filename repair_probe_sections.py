from pathlib import Path

PATH = Path(__file__).with_name("probe_sections.py")

HELPER_ANCHOR = "\ndef _dark_components(image, box):\n"
HELPER = r'''

def rating_document_nodes(nodes, client_rect, screenshot_path, window_rect):
    """Return only the live rating Document subtree.

    WeChat/Chromium keeps old mini-program pages alive in UI Automation. Those
    stale trees can occupy the same screen coordinates as the live form, which
    previously mixed roster names/calendar text into the five score rows.

    The live screenshot first selects the visibly rendered rating headings.
    We then require Making Skills and Problem Solving to belong to the same
    Document and keep only that Document's descendants for section/score
    mapping. No action is taken when that cannot be proven.
    """
    visible = [
        node for node in nodes
        if wkcommon.node_is_visibly_present(node, client_rect)[0]
    ]
    selected, support = ctx._select_visual_heading_nodes(
        visible, screenshot_path, window_rect)
    parents = ctx.build_parent_map(nodes)
    node_index = {id(node): i for i, node in enumerate(nodes)}
    doc_ids = {}
    for english, node in selected.items():
        if not support.get(english, {}).get("supported"):
            continue
        idx = node_index.get(id(node))
        if idx is None:
            continue
        doc_ids[english] = ctx.document_index_of(idx, nodes, parents)

    making_doc = doc_ids.get("Making Skills")
    problem_doc = doc_ids.get("Problem Solving")
    if making_doc is None or problem_doc is None:
        return [], "live Making/Problem document could not be identified"
    if making_doc != problem_doc:
        return [], "live Making/Problem headings resolve to different documents"

    same_doc = [name for name, doc in doc_ids.items() if doc == making_doc]
    if len(same_doc) < 3:
        return [], "only {} visually verified headings belong to chosen document".format(
            len(same_doc))

    root_depth = nodes[making_doc].get("depth", 0)
    subset = [nodes[making_doc]]
    for node in nodes[making_doc + 1:]:
        if node.get("depth", 0) <= root_depth:
            break
        subset.append(node)
    return subset, "Document {} with {} visually verified headings".format(
        making_doc, len(same_doc))
'''


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit("anchor not found for " + label)
    return text.replace(old, new, 1)


def main():
    text = PATH.read_text(encoding="utf-8")

    if "def rating_document_nodes(" not in text:
        text = replace_once(text, HELPER_ANCHOR, HELPER + HELPER_ANCHOR,
                            "rating_document_nodes helper")

    old = '''    mapping = wkcommon.map_rating_sections(nodes)\n    sections = mapping["sections"]\n    emit("")\n    emit("== state on arrival ==")\n    states = []\n    for section in sections:\n        measured = section_state(nodes, section)\n'''
    new = '''    rating_nodes, rating_doc_reason = rating_document_nodes(\n        nodes, client_rect, precheck_shot, window_rect)\n    emit("active rating tree: {}".format(rating_doc_reason))\n    if not rating_nodes:\n        emit("ABORT: could not isolate the live rating Document. Nothing was clicked.")\n        with open(report_path, "w", encoding="utf-8") as fh:\n            fh.write("\\n".join(out) + "\\n")\n        print("Live rating Document could not be isolated - nothing clicked.")\n        print("REPORT: {}".format(report_path))\n        return 3\n\n    mapping = wkcommon.map_rating_sections(rating_nodes)\n    sections = mapping["sections"]\n    if len(sections) != 5 or not mapping["order_ok"]:\n        emit("ABORT: live rating Document did not map to five ordered sections.")\n        with open(report_path, "w", encoding="utf-8") as fh:\n            fh.write("\\n".join(out) + "\\n")\n        print("Rating sections could not be mapped - nothing clicked.")\n        print("REPORT: {}".format(report_path))\n        return 3\n    emit("")\n    emit("== state on arrival ==")\n    states = []\n    for section in sections:\n        measured = section_state(rating_nodes, section)\n'''
    if old in text:
        text = replace_once(text, old, new, "initial active document mapping")

    text = text.replace('        before = section_state(nodes, section)\n',
                        '        before = section_state(rating_nodes, section)\n')

    old = '''            nodes = wkcommon.walk_described(wrapper, args.max_nodes)\n            postcheck_shot = os.path.join(\n                shots, "{}_{}_postcheck.png".format(stamp, index + 1))\n            emit("  postcheck shot: {}".format(\n                wkcommon.capture_window(wrapper, postcheck_shot)))\n            state_after, _reasons, _signals = classify_current(\n                nodes, client_rect, postcheck_shot, window_rect)\n'''
    new = '''            nodes = wkcommon.walk_described(wrapper, args.max_nodes)\n            postcheck_shot = os.path.join(\n                shots, "{}_{}_postcheck.png".format(stamp, index + 1))\n            emit("  postcheck shot: {}".format(\n                wkcommon.capture_window(wrapper, postcheck_shot)))\n            state_after, _reasons, _signals = classify_current(\n                nodes, client_rect, postcheck_shot, window_rect)\n'''
    # Keep this block structurally unchanged; the next replacement inserts the
    # live-document remap immediately after the page-state check.

    old2 = '''            mapped_after = wkcommon.map_rating_sections(nodes)\n            if not mapped_after["sections"] or not mapped_after["order_ok"]:\n'''
    new2 = '''            rating_nodes, rating_doc_reason = rating_document_nodes(\n                nodes, client_rect, postcheck_shot, window_rect)\n            emit("  active rating tree: {}".format(rating_doc_reason))\n            if not rating_nodes:\n                entry["state_after"] = "MAPPING-FAILED"\n                entry["aborted"] = "live rating Document disappeared after expansion"\n                emit("  ABORT: {}; stopping.".format(entry["aborted"]))\n                findings.append(entry)\n                break\n            mapped_after = wkcommon.map_rating_sections(rating_nodes)\n            if not mapped_after["sections"] or not mapped_after["order_ok"]:\n'''
    if old2 in text:
        text = replace_once(text, old2, new2, "post-click active document mapping")

    text = text.replace('            after = section_state(nodes, section)\n',
                        '            after = section_state(rating_nodes, section)\n')
    text = text.replace('            section_state(nodes, other)["state"]\n',
                        '            section_state(rating_nodes, other)["state"]\n')

    marker = '''        shot_after = os.path.join(shots, "{}_{}_after.png".format(stamp, index + 1))\n'''
    if 'entry["state_after"] = after["state"]' not in text:
        text = replace_once(
            text, marker,
            '        entry["state_after"] = after["state"]\n' + marker,
            "state_after bookkeeping")

    PATH.write_text(text, encoding="utf-8")
    print("probe_sections.py repaired: live Document filtering + state bookkeeping")


if __name__ == "__main__":
    main()
