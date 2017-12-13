#!/usr/bin/env python3

import datetime
from collections import Counter
from flask import Flask, request, render_template, redirect, abort
import json
import redis
from redis import WatchError
from urllib.parse import urlparse

# globals
app = Flask(__name__)
DB = None
BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


@app.route("/", methods=["GET", "POST"])
def index():
    """Main view
    Handles form submission via POST
    """
    if request.method == "POST":
        url = request.form.get("url")

        if urlparse(url).scheme in ["http", "https"]:
            hashed_id = add_new_link(url)
            return redirect('/stats/' + hashed_id)
        else: 
            abort(400)
    else:
        return render_template('index.html')


@app.route("/stats/<string:uid>")
def statistics(uid):
    """Return url statistics

    Arguments:
    - `uid`: The hashed id of the url
    """
    if not DB.exists(uid):
        abort(404)
    else:
        entry = DB.get(uid)
        entry = json.loads(entry)

        short_link = uid
        full_link = entry["url"]
        clicks = entry["traffic"]
        timestamp = entry["time"]

        hourly_visits = map(lambda ts: (ts//(60*60)) * (60*60), (int(click["timestamp"]) for click in clicks))
        hourly_visits = Counter(hourly_visits)

        referrers = [sanitize_referers(click["referrer"]) for click in clicks]
        referrers = Counter(referrers)

        UA = [click["UA"] for click in clicks]
        user_agents = {
            "Chrome": 0,
            "Firefox": 0,
            "Safari": 0,
            "Opera": 0,
            "Others": 0
        }

        for ua_string in UA:
            if "chrome" in str.lower(ua_string):
                user_agents["Chrome"] += 1
            elif "firefox" in str.lower(ua_string):
                user_agents["Firefox"] += 1
            elif "safari" in str.lower(ua_string):
                user_agents["Safari"] += 1
            elif "opera" in str.lower(ua_string):
                user_agents["Opera"] += 1
            else:
                user_agents["Others"] += 1

        return render_template('stat.html', 
                               short_link=short_link, 
                               full_link=full_link, 
                               clicks=clicks,
                               creation_time=timestamp, 
                               daily_visits=hourly_visits,
                               referrers=referrers,
                               user_agents=user_agents)


@app.route("/<string:uid>")
def link_to(uid):
    """Redirect to url

    Arguments:
    - `uid`: The hashed id of the url
    """
    if not DB.exists(uid):
        abort(404)
    else:
        entry = DB.get(uid)
        entry = json.loads(entry)
        
        url = entry["url"]
        referrer = request.referrer if request.referrer is not None else "Unknown"
        visitor = {
                    "UA": request.headers.get('User-Agent'), 
                    "timestamp": utc_now(),
                    "referrer": referrer
                    }
        entry["traffic"].append(visitor)

        serialised_entry = json.dumps(entry)
        DB.set(uid, serialised_entry)

        return redirect(url)


def encode(num, alphabet=BASE62):
    """Encode a positive number in Base X

    Arguments:
    - `num`: The number to encode
    - `alphabet`: The alphabet to use for encoding
    """
    if num == 0:
        return alphabet[0]
    arr = []
    base = len(alphabet)
    while num:
        num, rem = divmod(num, base)
        arr.append(alphabet[rem])
    arr.reverse()
    return ''.join(arr)


def decode(string, alphabet=BASE62):
    """Decode a Base X encoded string into the number

    Arguments:
    - `string`: The encoded string
    - `alphabet`: The alphabet to use for encoding
    """
    base = len(alphabet)
    strlen = len(string)
    num = 0

    idx = 0
    for char in string:
        power = (strlen - (idx + 1))
        num += alphabet.index(char) * (base ** power)
        idx += 1

    return num


def sanitize_referers(url):
    """Given a url sanitize it to a referrer

    Arguments:
    - `url`: An url
    """
    if url is None:
        return "Unknown"
    elif "google." in url:
        return url
    elif "facebook." in url or "fb." in url:
        return url
    else:
        return "Unknown" 


def checksum(num, length, modulus):
    """Returns base `modulus` checksum of a number `num` of length `length

    Arguments:
    - `num`: The number to hash
    - `modulus`: Base to hash in
    - `length`: Max length of hashed output 
    """
    mod = modulus ** length
    product = 1
    for x in str(num):
        if x != "0":
            product = ((product%mod) * (int(x)%mod))%mod
    return encode(product)


def add_new_link(url):
    """Perform transaction to add new link

    Arguments:
    - `url`: A valid url
    """
    with DB.pipeline() as pipe:
        while True:
            hashed_id = None
            try:
                # Watch `max_id` for changes
                pipe.watch("max_id")
                # after WATCHing, the pipeline is put into immediate execution
                # mode until we tell it to start buffering commands again.
                # this allows us to get the current value of our sequence

                # Get current `max_id`
                current_max_id = DB.get("max_id")
                next_max_id = int(current_max_id) + 1
                hashed_id = encode(next_max_id) + checksum(next_max_id, 2, 62)
                timestamp = utc_now()
                entry = {
                    "url": url,
                    "traffic": [],
                    "time": timestamp
                }
                serialised_entry = json.dumps(entry)

                # now we can put the pipeline back into buffered mode with MULTI
                pipe.multi()
                pipe.set("max_id", next_max_id)
                pipe.set(hashed_id, serialised_entry)

                # and finally, execute the pipeline (the set command)
                pipe.execute()

                # if a WatchError wasn't raised during execution, everything
                # we just did happened atomically.
                return hashed_id
            except WatchError:
                # another client must have changed `max_id` between
                # the time we started WATCHing it and the pipeline's execution.
                # our best bet is to just retry.
                continue


def init():
    """Initialise redis database
    """
    global DB, max_id
    DB = redis.StrictRedis(host='localhost', port=6379, db=0)

    if not DB.exists("max_id"):
        DB.set("max_id", "411757")


def utc_now():
    """Return current utc timestamp
    """
    now = datetime.datetime.utcnow()
    return int(now.strftime("%s"))

init()

if __name__ == "__main__":
    app.run(debug=True)


