"""Public-safety audit — fail loud if any secret pattern leaked into the repo."""
import pathlib

SECRETS_DENYLIST = [
    # Internal Aspire hostnames
    "AZCSAPPRODB01", "AZCPOSPRODAPP01", "aspirezone.qa", ".aspire.qa",
    "azfpictures",
    # Real IPs (Oracle VM + Hetzner)
    "129.151.146.100", "65.21.249.9",
    # Real Connect content GUIDs (pseudo-secret)
    "bbda9424-decb", "52f29754", "cb97879f", "08ba41b5", "296488c2",
    "15b8c70c", "c12b9927", "37cbdc4d", "a84f02d1",
    # Real key fragments from CLAUDE.md
    "sc-bJPunFr", "9a3vtlB3kLgwe5aPU", "fuIDM2kOycFqgq3uFWp8",
    # Aiven cluster patterns
    "avnadmin:",
    # Telegram bot
    "KennyMcM_bot",
    # OpenAI / Anthropic / OpenRouter
    "sk-proj-", "sk-ant-",
    # Public-but-Aspire-owned hosts — keep out of the lib code, env-only
    "qatar-sports-analytics.duckdns.org",
]

# Allowed PUBLIC names that may appear (avoid false positives)
ALLOWED_SUBSTRINGS = (
    "qatar-sports-analytics.duckdns.org",
    "posit.aspire.qa",                      # public DNS, default in env example
    "mirror.kakao.com",
    "github.com/kennymcmillan",
)

issues = []
files_scanned = []
for path in pathlib.Path(".").rglob("*"):
    if path.is_dir():
        continue
    if any(p in path.parts for p in (".git", "__pycache__", ".venv")):
        continue
    if path.name == "_audit.py":  # self-reference is expected
        continue
    if path.suffix not in (".py", ".md", ".txt", ".yml", ".yaml", ".example", ""):
        continue
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    files_scanned.append(str(path))
    for bad in SECRETS_DENYLIST:
        if bad in src:
            line_no = src[: src.index(bad)].count("\n") + 1
            issues.append(f"{path} L{line_no}: contains {bad!r}")

print(f"Scanned {len(files_scanned)} files.")
if issues:
    print(f"\n!!! FOUND {len(issues)} POTENTIAL LEAKS !!!")
    for i in issues:
        print(f"  {i}")
    raise SystemExit(1)
print("CLEAN — no internal hostnames, real GUIDs, or key fragments found.")
print(f"Allowed public DNS / mirror names: {ALLOWED_SUBSTRINGS}")
