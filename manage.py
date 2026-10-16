"""Operator commands for the Re-Exam Scheduler (run on the server, from this folder).

  python manage.py reset-link <email-or-admin-username>
        Print a one-time, time-limited password-reset link for an account.
        Use this when e-mail delivery is not configured (see README).
        Anyone who can run this command already controls the server.

  python manage.py migrate
        Remove obsolete recovery-answer data from existing account files.
        Safe to run repeatedly; it never deletes accounts. (It also runs
        automatically when the app starts.)
"""
import sys

from app import app
import auth


def main(argv):
    if len(argv) >= 2 and argv[1] == "migrate":
        with app.app_context():
            removed = auth.migrate_legacy_fields()
        print(f"Removed recovery-answer data from {removed} record(s).")
        return 0
    if len(argv) == 3 and argv[1] == "reset-link":
        with app.app_context():
            link = auth.create_reset_link_for(argv[2])
        if not link:
            print("No account found for that email/username.", file=sys.stderr)
            return 1
        print(f"One-time reset link (valid {auth.reset_token_minutes()} minutes, single use):\n{link}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
