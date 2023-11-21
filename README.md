# pr-review-stats

Use this tool to analyze code review delays in your github repos.

The project has no command line flags, and expects you to hardcode all the variables.

Uses Github's GraphQL API to collect the information in 2 steps:

1. Get a of PRs
2. Get timeline of review-related events for each of the PRs.

To work around Github's queries-per-hour restriction, the tool serializes API
responses on disk. Each successful response is stored in a separate file in a directory `./db`.

If you want to re-run the analysis at a later point in time, you'll need to delete some of the cached responses:
```
rm ./db/get_prs_*
```

## Run the tool
```json
GITHUB_TOKEN="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" python3 ./main.py
```

and get the results in a csv-format printed to stdout.
