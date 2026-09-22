"""Local-only diagnostics; default mode never clicks or scrolls."""
import argparse
import json
import os

import wkcommon
from humanlike_engine import HumanLikeRatingSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover', action='store_true',
                        help='Run bounded read-only ability discovery (scrolls).')
    parser.add_argument('--navigate', action='store_true',
                        help='Navigate Home to an exact class; never rate or submit.')
    parser.add_argument('--date')
    parser.add_argument('--time')
    parser.add_argument('--class-name')
    args = parser.parse_args()
    session = HumanLikeRatingSession(raise_window=args.discover)
    snap = session.snapshot('diagnose_live')
    payload = dict(snap, window=session.window_rect, client=session.client_rect)
    path = os.path.join(session.reports_dir, 'diagnose_live.json')
    with open(path, 'w', encoding='utf-8') as output:
        json.dump(payload, output, ensure_ascii=False, indent=2,
                  default=lambda obj: '<{}>'.format(type(obj).__name__))
    print('Snapshot:', snap['path'])
    print('Geometry:', session.window_rect, session.client_rect)
    print('Headings:', snap['support'])
    print('Visible text:', [node['name'] for node in session._visible(snap['nodes'])
                            if node.get('name')])
    if args.discover:
        print('Discovered:', session.discover_categories())
    if args.navigate:
        if not all((args.date, args.time, args.class_name)):
            parser.error('--navigate requires --date, --time and --class-name')
        from home_navigator import WowkidsHomeNavigator
        from wowkids_cloud_agent import _matches_job, _roster_identity
        from class_controller import RosterNavigator
        job = {'target_date': args.date, 'class_time': args.time,
               'wowkids_class_name': args.class_name}
        nav = WowkidsHomeNavigator()
        print('Navigation:', nav.navigate_to_roster(job))
        matched, reason = _matches_job(_roster_identity(RosterNavigator()), job)
        print('Exact class match:', matched, reason)
        if not matched:
            raise RuntimeError(reason)


if __name__ == '__main__':
    main()
