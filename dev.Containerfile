FROM docker.io/library/python:3.14-trixie

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG USER_ID=1000
ARG GROUP_ID=1000

# Install shared dependencies (same as simple.Containerfile, but the
# pistack-controller project is NOT installed: source is mounted at runtime
# and run fresh with `uv run` so edits take effect without rebuilding).
USER root
RUN export DEBIAN_FRONTEND=noninteractive &&\
	apt-get update && \
	apt-get install -y \
	git \
	perl \
	grokmirror \
	sqlite3 \
	curl \
	make \
	build-essential \
	# xapian search
	xapian-omega \
	libsearch-xapian-perl \
	lei \
	yq \
	# public inbox
	public-inbox \
	libplack-perl \
	libinline-c-perl \
	libemail-address-xs-perl \
	libplack-middleware-reverseproxy-perl \
	libhighlight-perl \
	libnet-server-perl \
	libmail-imapclient-perl \
	spamd \
	spamc

# Keep the project venv OUT of the mounted source dir so `uv run` does not
# clobber the host's pistack-controller/.venv. Make it writable by the
# container user.
ENV UV_PROJECT_ENVIRONMENT=/pistack-venv
RUN mkdir -p /pistack-venv && chown -R "${USER_ID}:${GROUP_ID}" /pistack-venv

# uv cache dir (writable by the container user)
RUN mkdir -p /tmp/.cache/uv && chown -R "${USER_ID}:${GROUP_ID}" /tmp/.cache/uv

ENV UV_CACHE_DIR=/tmp/.cache/uv
WORKDIR /pistack-controller
