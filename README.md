# MCG Signal: profile and shared rules

This repository holds the public profile of the organization and the rules that all its repositories share.

| File | What it is |
| --- | --- |
| [profile/README.md](profile/README.md) | The profile page at [github.com/mcgsignal](https://github.com/mcgsignal). |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The rules for a change. |
| [SECURITY.md](SECURITY.md) | How to report a security problem. |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | The code of conduct. |
| [tools/check.py](tools/check.py) | The check that we do before and after each change. |

## The check

Do the check before you open a pull request, and again after the merge:

```
python tools/check.py local <repository folder> --title "<pull request title>"
python tools/check.py live
```

The first command reads a working copy. The second command reads what the public sees: the files, the settings and the pages of each public repository. It needs the GitHub CLI and git.
