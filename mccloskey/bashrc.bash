REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
git status
source "$REPO_ROOT/mccloskey/aliases.bash"