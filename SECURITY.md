# Security policy

## What this server is

py-exec-mcp executes arbitrary Python with the full privileges of the process that launched
it. That is its purpose, and it is not sandboxed. "Code run through the tool can read files,
reach the network or modify the system" is the documented behaviour, not a vulnerability.
Isolation has to come from the layer underneath: a container, a VM, a scrubbed environment.

## What counts as a vulnerability

Anything that breaks a promise the README makes. For example:

- code reaching the interpreter altered in any way (quoting, encoding, newline handling)
- a timeout that does not end the whole process tree
- output truncation that is silent, or a cap that can be exceeded in memory

## Reporting

Use GitHub's private vulnerability reporting on this repository ("Security" tab, "Report a
vulnerability"). Please do not open a public issue for something exploitable.

You will get an acknowledgement within a week. Fixes ship as a patch release with a
CHANGELOG entry crediting the reporter unless they prefer otherwise.

## Supported versions

The latest release on PyPI. Older releases are not patched.
