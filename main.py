import os
import requests
import json
from dateutil.parser import isoparse
from datetime import datetime, timedelta

token = os.getenv('GITHUB_TOKEN')

if not token:
    raise EnvironmentError("The GITHUB_TOKEN environment variable is not set.")

headers = {
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/json'
}

NO_RESPONSE = 1
UNSOLICITED = 2
RESPONDED = 4

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
            ISSUE_COMMENT,
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
                  ... on IssueComment {
                    author {
                      login
                    }
                    createdAt
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
                # print(f"Found value {keys}")
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
        print(response.json())
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
    repo = f'{owner}/{name}'
    for pr in get_all_prs(owner, name):
        pr_id = pr["node"]["number"]
        pr_timeline = get_pr_timeline(owner, name, pr_id)
        result = analyze_pr_timeline(
            pr_timeline["data"]["repository"]["pullRequest"], repo)
        if result is None:
            continue
        author, number, published_at, reviews = result
        yield (owner, name, author, number, published_at, reviews)


def parse_datetime(s):
    return isoparse(s)


def is_business_day(date, user):
    if date > parse_datetime('2022-12-26T00:00:00Z') and date < parse_datetime(
            '2023-01-01T00:00:00Z'):
        return False
    if date > parse_datetime('2023-07-02T00:00:00Z') and date < parse_datetime(
            '2023-07-09T00:00:00Z'):
        return False
    return date.weekday() < 5


def business_days_between(start_date, end_date, user):
    if start_date.date() == end_date.date():
        if is_business_day(start_date, user):
            return 1
        else:
            return 0

    full_days_delta = int(
        (end_date - start_date).total_seconds()) // (24 * 60 * 60)
    holidays = 0
    day = timedelta(days=1)
    d = start_date + day
    while d.date() != end_date.date():
        if not is_business_day(d, user):
            holidays += 1
        d += day

    business_days = full_days_delta - holidays
    assert business_days >= 0
    if is_business_day(start_date, user) and is_business_day(end_date, user):
        return business_days + 1
    else:
        return business_days


class Review(object):

    def __init__(self, t1, t2, user, number, review_type, pr_author, repo):
        assert t1 <= t2
        self.t1 = t1
        self.t2 = t2
        self.user = user
        self.business_days = business_days_between(t1, t2, user)
        assert self.business_days >= 0
        self.seconds = int((t2 - t1).total_seconds())
        assert self.seconds >= 0
        self.number = number
        self.review_type = review_type
        self.pr_author = pr_author
        self.repo = repo

    def __str__(self):
        return str(
            f"business_days={self.business_days}, seconds={self.seconds}, user={self.user}, t1={self.t1}, t2={self.t2}, pr={self.number}, review_type={self.str_review_type(self.review_type)}, pr_author={self.pr_author}, repo={self.repo}"
        )

    def __repr__(self):
        return str(
            f"business_days={self.business_days}, seconds={self.seconds}, user={self.user}, t1={self.t1}, t2={self.t2}, pr={self.number}, review_type={self.str_review_type(self.review_type)}, pr_author={self.pr_author}, repo={self.repo}"
        )

    def str_review_type(self, review_type):
        if review_type == NO_RESPONSE:
            return "NO_RESPONSE"
        elif review_type == UNSOLICITED:
            return "UNSOLICITED"
        elif review_type == RESPONDED:
            return "RESPONDED"
        else:
            assert False

    def csv_header(self):
        return "business_days,wall_time_seconds,reviewer,t1,t2,pr,review_type,author,repo"

    def csv(self):
        return ','.join([
            str(v) for v in [
                self.business_days, self.seconds, self.user, self.t1, self.t2,
                self.number,
                self.str_review_type(
                    self.review_type), self.pr_author, self.repo
            ]
        ])


def analyze_pr_timeline(timeline, repo):
    prev_event_created_at = None
    pr_author = timeline["author"]
    if pr_author is not None:
        pr_author = pr_author["login"]
    number = timeline["number"]
    published_at = parse_datetime(timeline["publishedAt"])
    # print(number, pr_author, published_at)
    outstanding_review_request_per_reviewer = {}
    reviews = []
    stop_at = None
    events = []

    for event in timeline["timelineItems"]["nodes"]:
        event_type = event["__typename"]
        if event_type == "AssignedEvent":
            continue
        created_at = parse_datetime(event["createdAt"])
        events.append((created_at, event))

    for (created_at, event) in sorted(events, key=lambda x: x[0]):
        event_type = event["__typename"]
        # print(event_type, created_at)
        if prev_event_created_at is not None:
            assert created_at >= prev_event_created_at
            prev_event_created_at = created_at
        match event_type:
            case "ReviewRequestRemovedEvent":
                reviewer = event["requestedReviewer"]
                if reviewer and "login" in reviewer:
                    reviewer = reviewer["login"]
                    if reviewer in outstanding_review_request_per_reviewer:
                        started_at = outstanding_review_request_per_reviewer[
                            reviewer]
                        reviews.append(
                            (Review(started_at, created_at, reviewer, number,
                                    RESPONDED, pr_author, repo), None, None))
                        del outstanding_review_request_per_reviewer[reviewer]
                        assert reviewer not in outstanding_review_request_per_reviewer
            case "ReviewRequestedEvent":
                # assert stop_at is None
                reviewer = event["requestedReviewer"]
                if reviewer and "login" in reviewer:
                    reviewer = reviewer["login"]
                    if reviewer not in outstanding_review_request_per_reviewer:
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
                        reviewer]
                    del outstanding_review_request_per_reviewer[reviewer]
                    assert reviewer not in outstanding_review_request_per_reviewer
                    reviews.append((Review(started_at, created_at, reviewer,
                                           number, RESPONDED, pr_author,
                                           repo), state, num_comments))
                else:
                    reviews.append((Review(published_at, created_at, reviewer,
                                           number, UNSOLICITED, pr_author,
                                           repo), state, num_comments))
            case "IssueComment":
                reviewer = event["author"]
                if reviewer is None:
                    continue
                reviewer = reviewer["login"]
                if reviewer == pr_author:
                    continue
                if reviewer in outstanding_review_request_per_reviewer:
                    started_at = outstanding_review_request_per_reviewer[
                        reviewer]
                    del outstanding_review_request_per_reviewer[reviewer]
                    assert reviewer not in outstanding_review_request_per_reviewer
                    reviews.append(
                        (Review(started_at, created_at, reviewer, number,
                                RESPONDED, pr_author, repo), None, None))
                else:
                    reviews.append(
                        (Review(published_at, created_at, reviewer, number,
                                UNSOLICITED, pr_author, repo), None, None))
            case "MergedEvent" | "ClosedEvent":
                stop_at = created_at
            case "ReadyForReviewEvent":
                pass
    for reviewer in outstanding_review_request_per_reviewer:
        created_at = outstanding_review_request_per_reviewer[reviewer]
        reviews.append((Review(created_at, stop_at, reviewer, number,
                               NO_RESPONSE, pr_author, repo), None, None))
    return pr_author, number, published_at, reviews


class UserStats(object):

    def __init__(self, user, n):
        self.user = user
        self.buckets = []
        for i in range(n):
            self.buckets.append([])

    def add(self, review):
        # assert review.user == self.user
        if review.business_days >= len(self.buckets):
            self.buckets[-1].append(review)
        else:
            self.buckets[review.business_days].append(review)

    def __str__(self):
        return str(
            f"User={self.user}, Buckets={' '.join([str((i,len(bucket))) for (i,bucket) in enumerate(self.buckets)])}"
        )

    def get_prs(self, business_days, types):
        assert business_days >= 0 and business_days < len(self.buckets)
        reviews = []
        for review in self.buckets[business_days]:
            if (review.review_type & types
                ) != 0 and review.t1 > parse_datetime('2022-11-01T00:00:00Z'):
                reviews.append(review)
        return reviews

    def get_num_prs(self, business_days, types):
        return len(self.get_prs(business_days, types))

    def get_all_prs(self):
        for b in self.buckets:
            for r in b:
                yield r


def summarize(reviews):
    user_stats = {}
    num_buckets = 10
    any_review = None
    for pr in reviews:
        for review in pr[5]:
            review = review[0]
            if not any_review:
                any_review = review
            user = review.user
            if user not in user_stats:
                user_stats[user] = UserStats(user, num_buckets)
            user_stats[user].add(review)

    #for user in sorted(user_stats.keys()):
    #    s = user_stats[user]
    #    print(user, [
    #        s.get_num_prs(d, RESPONDED | NO_RESPONSE)
    #        for d in range(num_buckets)
    #    ])

    #for user in sorted(user_stats.keys()):
    #    s = user_stats[user]
    #    print(user,
    #          [s.get_num_prs(d, UNSOLICITED) for d in range(num_buckets)])

    #for d in range(0, 10):
    #    print(f"days: {d} UNSOLICITED")
    #    for q in sorted(user_stats[''].get_prs(d, UNSOLICITED),
    #                    key=lambda x: x.number):
    #        print(f"{q}")
    print(any_review.csv_header())
    for user in user_stats:
        for review in user_stats[user].get_all_prs():
            if review.t1 > parse_datetime('2023-08-01T00:00:00Z'):
                print(review.csv())


def main():
    reviews = []
    for (owner, name) in [('near', 'NEPs'), ('near', 'near-ops'),
                          ('near', 'nearcore')]:
        for r in analyze_repo(owner, name):
            reviews.append(r)
    summarize(reviews)


if __name__ == '__main__':
    main()
