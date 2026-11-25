# DNS lab images: the DNS servers companies run, from Alpine's packages, plus curl for cat-egory-dns-update.
# Lab only: the servers run as root and without DNSSEC validation, to keep the configs about the block lists.
FROM alpine:3.24 AS bind
RUN apk add --no-cache bind bind-tools curl

FROM alpine:3.24 AS unbound
RUN apk add --no-cache unbound curl

FROM alpine:3.24 AS pdns
RUN apk add --no-cache pdns-recursor curl

FROM python:3.12.16-alpine AS feeds
COPY feedserver.py /lab/feedserver.py
CMD ["python", "/lab/feedserver.py", "8000"]
