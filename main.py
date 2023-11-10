import os
import requests
import json
from dateutil.parser import isoparse

token = os.getenv('GITHUB_TOKEN')

if not token:
    raise EnvironmentError("The GITHUB_TOKEN environment variable is not set.")

headers = {
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/json'
}

all_prs_query = """
query($owner:String!, $name:String!, $afterCursor:String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: [CLOSED,MERGED], first: 100, orderBy: {field: CREATED_AT, direction: DESC}, after: $afterCursor) {
      edges {
        node {
          id
          title
          number
          createdAt
          mergedAt
          closedAt
          author {
            login
          }
        }
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""

pr_query = """
query($pr:Int!, $owner:String!, $name:String!) {
  repository(owner: $owner, name: $name) {
      pullRequest(number: $pr) {
          publishedAt
          author {
            login
          }
          closed
          closedAt
          createdAt
          isDraft
          merged
          mergedAt
          number
          reviewDecision
          reviewRequests {
            totalCount
          }
          state
          timelineItems(itemTypes: [
            ASSIGNED_EVENT,
            CLOSED_EVENT,
            CONVERT_TO_DRAFT_EVENT,
            MERGED_EVENT,
            PULL_REQUEST_REVIEW,
            PULL_REQUEST_REVIEW_THREAD,
            READY_FOR_REVIEW_EVENT,
            REVIEW_DISMISSED_EVENT,
            REVIEW_REQUESTED_EVENT,
            REVIEW_REQUEST_REMOVED_EVENT,
            ], first: 250) {
              nodes {
                  __typename
                  ... on ClosedEvent {
                    createdAt
                    stateReason
                    actor {
                      login
                    }
                  }
                  ... on ConvertToDraftEvent {
                    createdAt
                    actor {
                       login
                    }
                  }
                  ... on MergedEvent {
                    createdAt
                    actor {
                      login
                    }
                  }
                  ... on PullRequestReview {
                    createdAt
                    author {
                      login
                    }
                    comments {
                      totalCount
                    }
                    state
                  }
                  ... on PullRequestReviewThread {
                    comments {
                      totalCount
                    }
                    resolvedBy {
                      login
                    }
                    subjectType
                  }
                  ... on ReadyForReviewEvent {
                    createdAt
                    actor {
                       login
                    }
                  }
                  ... on ReviewDismissedEvent {
                    actor {
                      login
                    }
                    createdAt
                    previousReviewState            
                  }
                  ... on ReviewRequestedEvent {
                    createdAt
                    actor {
                      login
                    }
                    requestedReviewer {
                      ... on User {
                        login
                      }
                      ... on Team {
                        name
                      }
                    }
                  }
                  ... on ReviewRequestRemovedEvent {
                    createdAt
                    actor {
                       login
                    }
                    requestedReviewer {
                      ... on User {
                        login
                      }
                      ... on Team {
                        name
                      }
                    }
                  }
              }
          }
      }
  }
}
"""


class DB(object):

    def get(self, keys):
        db_key = '_'.join(keys)
        file_path = f'db/{db_key}'
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                print(f"Found value {keys}")
                return json.load(f)
        print(f"Value not found {keys}")
        return None

    def set(self, keys, value):
        db_key = '_'.join(keys)
        file_path = f'db/{db_key}'
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(value, f)
            print(f"Set value {keys}")


def execute_query(query, vars):
    response = requests.post('https://api.github.com/graphql',
                             json={
                                 'query': query,
                                 'variables': vars
                             },
                             headers=headers)
    if response.status_code == 200:
        result = response.json()
        if "errors" in result:
            print(result)
        else:
            print(f"Got response {vars}")
            return result
    else:
        print("Query failed to run by returning code of {}. {}".format(
            response.status_code, query))
    raise 1
    return None


def get_cached_or_execute(query, vars, name):
    keys = [name] + [str(vars[key]) for key in sorted(vars.keys())]
    db = DB()
    value = db.get(keys)
    if value is None:
        value = execute_query(query, vars)
        assert value
        db.set(keys, value)
    return value


def get_prs(owner, name, afterCursor):
    vars = {
        'owner': owner,
        'name': name,
        'afterCursor': afterCursor,
    }
    return get_cached_or_execute(all_prs_query, vars, name=get_prs.__name__)


def get_all_prs(owner, name):
    after_cursor = None
    has_next_page = True
    while has_next_page:
        response = get_prs(owner, name, after_cursor)
        page_info = response["data"]["repository"]["pullRequests"]["pageInfo"]
        has_next_page = page_info["hasNextPage"]
        after_cursor = page_info["endCursor"]
        for node in response["data"]["repository"]["pullRequests"]["edges"]:
            yield node


def get_pr_timeline(owner, name, pr):
    vars = {
        'owner': owner,
        'name': name,
        'pr': pr,
    }
    return get_cached_or_execute(pr_query, vars, name=get_pr_timeline.__name__)


def analyze_repo(owner, name):
    for pr in get_all_prs(owner, name):
        pr_id = pr["node"]["number"]
        pr_timeline = get_pr_timeline(owner, name, pr_id)
        result = analyze_pr_timeline(
            pr_timeline["data"]["repository"]["pullRequest"])
        if result is None:
            continue
        author, number, published_at, latencies, unsolicited_reviews, unresponded_requests = result
        yield (owner, name, author, number, published_at, latencies,
               unsolicited_reviews, unresponded_requests)


def parse_datetime(s):
    return isoparse(s)


def analyze_pr_timeline(timeline):
    prev_event_created_at = None
    author = timeline["author"]
    if author is None:
        return None
    author = author["login"]
    number = timeline["number"]
    published_at = parse_datetime(timeline["publishedAt"])
    print(number, author, published_at)
    outstanding_review_request_per_reviewer = {}
    latencies = []
    stop_at = None
    unsolicited_reviews = []
    unresponded_requests = []
    for event in timeline["timelineItems"]["nodes"]:
        event_type = event["__typename"]
        if event_type == "AssignedEvent":
            continue
        created_at = parse_datetime(event["createdAt"])
        print(event_type, created_at)
        if prev_event_created_at is not None:
            assert created_at >= prev_event_created_at
            prev_event_created_at = created_at
        match event_type:
            case "ReviewRequestEvent":
                assert stop_at is None
                reviewer = event["requestedReviewer"]
                if "login" in reviewer:
                    reviewer = reviewer["login"]
                    assert reviewer not in outstanding_review_request_per_reviewer
                    outstanding_review_request_per_reviewer[
                        reviewer] = created_at
            case "PullRequestReview":
                state = event["state"]
                reviewer = event["author"]
                if reviewer is None:
                    continue
                reviewer = reviewer["login"]
                num_comments = event["comments"]["totalCount"]
                if reviewer in outstanding_review_request_per_reviewer:
                    started_at = outstanding_review_request_per_reviewer[
                        "reviewer"]
                    delay = created_at - started_at
                    del outstanding_review_request_per_reviewer["reviewer"]
                    assert reviewer not in outstanding_review_request_per_reviewer
                    latencies.append((reviewer, delay, state, num_comments))
                else:
                    delay = created_at - published_at
                    unsolicited_reviews.append(
                        (reviewer, delay, state, num_comments))
            case "MergedEvent" | "ClosedEvent":
                stop_at = created_at
            case "ReadyForReviewEvent":
                pass
    for reviewer in outstanding_review_request_per_reviewer:
        created_at = outstanding_review_request_per_reviewer[reviewer]
        unresponded_requests.append((reviewer, stop_at - created_at))
    return author, number, published_at, latencies, unsolicited_reviews, unresponded_requests


def main():
    stats = []
    for (owner, name) in [('near', 'NEPs'), ('near', 'near-ops'),
                          ('near', 'nearcore')]:
        for item in analyze_repo(owner, name):
            stats.append(item)
    print(stats)


if __name__ == '__main__':
    main()
