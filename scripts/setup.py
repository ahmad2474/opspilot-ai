#!/usr/bin/env python3
"""Interactive setup wizard (docs/opspilot-ai-roadmap-phase2.md Section
4.3) -- from `git clone` to a running app with minimal manual steps.
Run from the repo root: `python3 scripts/setup.py` (or `bash
scripts/setup.sh`, a thin wrapper that just calls this).

What this can automate: copying both .env example files, generating and
syncing AUTH_SHARED_SECRET to *both* frontend and backend, generating
NEXTAUTH_SECRET, hashing the admin password once (base64-encoded, see
write_admin_credentials()'s docstring for why) and writing it to both
files where each side needs it, actually running the DynamoDB
provisioning (scripts/provision_tables.py) instead of just printing the
commands, and (opt-in) creating a read-only IAM user via boto3 instead of
the console click-through.

What this can't automate: signing up for a Groq/Gemini/NVIDIA account and
generating those API keys -- external accounts only you can create. This
wizard prompts for them but can't produce them.

Every write goes through append_env(), which upserts by key -- re-running
this wizard after a failed step (or just to rotate a value) is safe and
never leaves duplicate KEY=... lines behind.
"""
from __future__ import annotations

import base64
import getpass
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import bcrypt
except ImportError:
    print(
        "Missing dependency 'bcrypt'. Install the backend's Python dependencies first:\n"
        "  cd opspilot-backend && pip install -r requirements.txt\n"
        "then re-run this wizard from the repo root."
    )
    raise SystemExit(1) from None

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print(
        "Missing dependency 'boto3'. Install the backend's Python dependencies first:\n"
        "  cd opspilot-backend && pip install -r requirements.txt\n"
        "then re-run this wizard from the repo root."
    )
    raise SystemExit(1) from None

# Resolved relative to this file, not the caller's CWD -- this wizard is
# documented as "run from the repo root", but there's no reason to make
# it silently do the wrong thing if someone runs it from elsewhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ENV = REPO_ROOT / "opspilot-backend" / ".env"
FRONTEND_ENV = REPO_ROOT / "opspilot-frontend" / ".env.local"
IAM_POLICY_PATH = REPO_ROOT / "docs" / "iam-policy.json"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "provision_tables.py"

# Must match app/services/{investigation,mcp_auth,audit_log}_service.py's
# real table names (app/core/config.py's Settings defaults) -- kept in
# sync with scripts/provision_tables.py's own TABLE_NAMES by hand since
# this file intentionally has no import dependency on the backend package
# (it must run before the backend's own dependencies are necessarily
# on PYTHONPATH).
TABLE_NAMES = ("opspilot-investigations", "opspilot-mcp-tokens", "opspilot-audit-log")


def ensure_env_files() -> None:
    pairs = [
        (REPO_ROOT / "opspilot-backend" / ".env.example", BACKEND_ENV),
        (REPO_ROOT / "opspilot-frontend" / ".env.local.example", FRONTEND_ENV),
    ]
    for example, real in pairs:
        if real.exists():
            print(f"{real.relative_to(REPO_ROOT)} already exists -- leaving it untouched.")
        else:
            shutil.copy(example, real)
            print(f"Created {real.relative_to(REPO_ROOT)}.")


def append_env(path: Path, key: str, value: str) -> None:
    """Upsert, not append-only -- replaces an existing KEY=... line if
    present, so re-running the wizard after a failed step (or just to
    rotate a value) doesn't leave duplicate keys behind.

    security-reviewer finding, 2026-09-03, fixed same day: a value
    containing an embedded newline (e.g. a credential with a stray
    newline from a clipboard paste) would silently inject an extra,
    attacker- or accident-controlled KEY=VALUE line into the file --
    live-verified the exploit shape before fixing it, not just reasoned
    about. A trailing newline (the common, harmless case -- most paste
    sources add one) is stripped silently; anything left after that is a
    genuine embedded newline and gets rejected with a clear error rather
    than silently written, since the caller almost certainly didn't mean
    to paste a multi-line value into a single env var.
    """
    value = value.rstrip("\r\n")
    if "\n" in value or "\r" in value:
        raise ValueError(
            f"Value for {key} contains an embedded newline -- this looks like a bad paste, "
            "not a real single-line value. Re-enter it without the line break."
        )
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}\n"
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    path.write_text("".join(lines))


def write_shared_secrets() -> None:
    shared = secrets.token_hex(32)
    for path in (BACKEND_ENV, FRONTEND_ENV):
        append_env(path, "AUTH_SHARED_SECRET", shared)
    # Separate from AUTH_SHARED_SECRET -- NextAuth's own session-cookie
    # signing key, frontend-only, never shared with the backend.
    append_env(FRONTEND_ENV, "NEXTAUTH_SECRET", secrets.token_urlsafe(32))
    print("Generated AUTH_SHARED_SECRET (synced to both files) and NEXTAUTH_SECRET.")


def write_admin_credentials() -> None:
    """Prompts once, writes to both files where each side needs it:
    ADMIN_EMAIL to both (frontend's login check + the backend's optional
    defense-in-depth `sub`-claim check), ADMIN_PASSWORD_HASH to the
    frontend only (the backend never reads it -- login happens in
    NextAuth, the backend only validates the resulting session token).

    The hash is base64-encoded before writing, matching
    opspilot-frontend/lib/auth.ts's own decode step exactly -- a raw
    bcrypt hash contains literal `$` characters
    (`$2b$12$...`), and Next.js's .env loader treats `$` as
    variable-expansion syntax, silently truncating an unencoded hash to
    nothing. bcrypt's own hash format is cross-language compatible
    (bcrypt.compare() on the frontend reads the cost factor embedded in
    the hash itself), so hashing here with Python's `bcrypt` instead of
    the frontend's `bcryptjs` is safe.
    """
    email = input("Admin email: ").strip()
    while not email:
        email = input("Admin email (required): ").strip()

    password = getpass.getpass("Admin password: ")
    while not password:
        password = getpass.getpass("Admin password (required): ")

    raw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    encoded_hash = base64.b64encode(raw_hash).decode()

    append_env(FRONTEND_ENV, "ADMIN_EMAIL", email)
    append_env(FRONTEND_ENV, "ADMIN_PASSWORD_HASH", encoded_hash)
    append_env(BACKEND_ENV, "ADMIN_EMAIL", email)
    print("Admin credentials hashed and written.")


def _create_iam_user() -> tuple[str, str, str] | None:
    """Returns (access_key_id, secret_access_key, region) on success, or
    None if the user isn't set up to auto-provision yet (no local AWS
    credentials configured for boto3 to act as)."""
    try:
        sts = boto3.client("sts")
        account_id = sts.get_caller_identity()["Account"]
    except (NoCredentialsError, ClientError) as exc:
        print(
            f"Couldn't determine your AWS account (run `aws configure` first, or pick manual "
            f"entry below): {exc}"
        )
        return None

    iam = boto3.client("iam")
    policy_doc = IAM_POLICY_PATH.read_text().replace("<YOUR_ACCOUNT_ID>", account_id)
    user_name = "opspilot-readonly"

    try:
        iam.create_user(UserName=user_name)
        print(f"Created IAM user '{user_name}'.")
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"IAM user '{user_name}' already exists -- reusing it.")

    iam.put_user_policy(UserName=user_name, PolicyName="OpspilotReadOnly", PolicyDocument=policy_doc)
    print("Attached the read-only policy from docs/iam-policy.json.")

    key = iam.create_access_key(UserName=user_name)["AccessKey"]
    region = input("AWS_REGION [us-east-1]: ").strip() or "us-east-1"
    print(
        "Access key generated -- AWS shows the secret key exactly once at creation time, "
        "this is the only chance to capture it automatically, and it's about to be written "
        "straight to opspilot-backend/.env rather than printed."
    )
    return key["AccessKeyId"], key["SecretAccessKey"], region


def setup_aws_credentials() -> None:
    print(
        "\nAWS credentials: the IAM user this creates/uses can only Describe/List/Get + "
        "read Pricing data -- see docs/iam-policy.json. It cannot create, modify, or delete "
        "anything in your account, by design, permanently."
    )
    auto = (
        input(
            "Create the read-only IAM user automatically via boto3? Needs `aws configure` "
            "already run with a principal that can create IAM users/policies/access keys "
            "-- a real trust decision, not the default. [y/N]: "
        )
        .strip()
        .lower()
    )

    if auto == "y":
        result = _create_iam_user()
        if result is not None:
            access_key_id, secret_access_key, region = result
            append_env(BACKEND_ENV, "AWS_ACCESS_KEY_ID", access_key_id)
            append_env(BACKEND_ENV, "AWS_SECRET_ACCESS_KEY", secret_access_key)
            append_env(BACKEND_ENV, "AWS_REGION", region)
            return
        print("Falling back to manual entry.\n")

    print(
        "Manual entry: IAM console -> Users -> Create user -> attach docs/iam-policy.json "
        "as a custom policy (fill in your account ID) -> Security credentials -> Create "
        "access key."
    )
    access_key_id = input("AWS_ACCESS_KEY_ID: ").strip()
    secret_access_key = getpass.getpass("AWS_SECRET_ACCESS_KEY: ")
    region = input("AWS_REGION [us-east-1]: ").strip() or "us-east-1"
    if not access_key_id or not secret_access_key:
        raise SystemExit(
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are required -- re-run once you have them."
        )
    append_env(BACKEND_ENV, "AWS_ACCESS_KEY_ID", access_key_id)
    append_env(BACKEND_ENV, "AWS_SECRET_ACCESS_KEY", secret_access_key)
    append_env(BACKEND_ENV, "AWS_REGION", region)


def setup_llm_keys() -> None:
    print(
        "\nLLM provider key(s) -- at least one required for chat to work. All three "
        "(Groq, Gemini, NVIDIA) have a free tier; the app tries them in that order and "
        "falls back automatically. Leave blank to skip a provider."
    )
    got_one = False
    for var, label in (("GROQ_API_KEY", "Groq"), ("GEMINI_API_KEY", "Gemini"), ("NVIDIA_API_KEY", "NVIDIA")):
        value = getpass.getpass(f"{label} API key (blank to skip): ").strip()
        if value:
            append_env(BACKEND_ENV, var, value)
            got_one = True
    if not got_one:
        print(
            "Warning: no LLM provider key entered -- chat won't work until at least one is "
            "set in opspilot-backend/.env. Everything else (galaxy dashboard, resource pages, "
            "login) works fine without one."
        )


def provision_tables() -> None:
    """Runs provision_tables.py as a subprocess, which inherits this
    process's environment by default -- but the AWS credentials just
    written by setup_aws_credentials() only went to the .env *file*, not
    this process's own os.environ, so the subprocess wouldn't see them
    without explicitly merging the just-written values in. Live-verified
    this gap by actually running it against a real account before fixing
    -- it failed with NoRegionError/NoCredentialsError otherwise."""
    print("\nProvisioning DynamoDB tables...")
    env = {**os.environ, **_load_env(BACKEND_ENV)}
    subprocess.run([sys.executable, str(PROVISION_SCRIPT)], check=True, env=env)


def _load_env(path: Path) -> dict[str, str]:
    """Re-reads what was actually written, rather than trusting this
    wizard's own in-memory values -- catches a bad write or stray typo
    the same way a fresh clone hitting a real bug would."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def smoke_test() -> bool:
    print("\nRunning smoke test...")
    env = _load_env(BACKEND_ENV)
    session = boto3.Session(
        aws_access_key_id=env.get("AWS_ACCESS_KEY_ID") or None,
        aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY") or None,
        region_name=env.get("AWS_REGION", "us-east-1"),
    )
    try:
        identity = session.client("sts").get_caller_identity()
        print(f"AWS credentials OK -- authenticated as {identity['Arn']}.")
    except Exception as exc:  # noqa: BLE001 - report and continue past this check
        print(f"AWS credential check FAILED: {exc}")
        print("Double-check AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in opspilot-backend/.env.")
        return False

    dynamodb = session.client("dynamodb")
    all_ok = True
    for table in TABLE_NAMES:
        try:
            dynamodb.describe_table(TableName=table)
            print(f"DynamoDB table '{table}' OK.")
        except dynamodb.exceptions.ResourceNotFoundException:
            print(f"DynamoDB table '{table}' not found -- did provisioning run cleanly?")
            all_ok = False
    return all_ok


def main() -> None:
    print("== OpsPilot AI setup ==\n")
    ensure_env_files()
    write_shared_secrets()
    write_admin_credentials()
    setup_aws_credentials()
    setup_llm_keys()
    provision_tables()

    if not smoke_test():
        print(
            "\n== Setup finished with errors above -- fix them, then re-run before starting "
            "the app. Re-running is safe: every value is upserted by key, so re-entering "
            "something just overwrites that one line instead of duplicating it. =="
        )
        raise SystemExit(1)

    print(
        "\n== Setup complete. ==\n"
        "  cd opspilot-backend && uvicorn app.main:app --reload\n"
        "  cd opspilot-frontend && npm install && npm run dev\n"
        "Then sign in at http://localhost:3000 with the admin credentials from this run."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup cancelled -- nothing after this point was written. Re-run any time.")
        raise SystemExit(1) from None
