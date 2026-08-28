FROM docker.io/library/python:3.14-trixie as base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG USER_ID=1000
ARG GROUP_ID=1000

# Install shared dependencies
USER root
RUN export DEBIAN_FRONTEND=noninteractive &&\
	apt-get update && \
	apt-get install -y \
	git \
	perl \
	# python3 \
	# python3-pip \
	sqlite3 \
	curl \
	make \
	build-essential \
	# xapian search
	xapian-omega \
	libsearch-xapian-perl \
    lei \
    yq \
	# public-inbox \
	libplack-perl \
	libinline-c-perl \
	libemail-address-xs-perl \
	libplack-middleware-reverseproxy-perl \
	libhighlight-perl \
	libnet-server-perl \
	libmail-imapclient-perl \
	libnet-server-perl \
	spamd \
	spamc

# install grokmirror from source
WORKDIR /grokmirror
COPY ./grokmirror/ /grokmirror/
RUN uv venv && uv pip install .

# install public-inbox from source
WORKDIR /public-inbox

COPY ./public-inbox/ /public-inbox/

RUN yes | ./install/deps.perl all

RUN perl Makefile.PL && \
	make && \
	make install

COPY ./pistack-controller /pistack-controller
RUN cd /pistack-controller && uv venv && uv pip install .

# Put grokmirror and pistack-controller console scripts on PATH
ENV PATH="/grokmirror/.venv/bin:/pistack-controller/.venv/bin:${PATH}"

WORKDIR /pistack-controller
