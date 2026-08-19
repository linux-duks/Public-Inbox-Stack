"""pistack-controller: a tinySupervisor workflow for the Public-Inbox-Stack.

Runs mirroring (grokmirror), indexing, lei imports and public-inbox serving
daemons inside a single container, orchestrated as a tinySupervisor DAG.
"""

__version__ = "0.1.0"
