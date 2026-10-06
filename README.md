# gh-usage-fetcher

A scheduled workflow that snapshots this account's GitHub Actions usage once a week.
It prints nothing but status codes; the snapshot is written to a private repository.
This repo is public only so the workflow runs on free public-repo minutes.

Needs one Actions secret, `USAGE_TOKEN`: a fine-grained token with account
permission **Plan: read** and **Contents: read and write** on the data repo.
