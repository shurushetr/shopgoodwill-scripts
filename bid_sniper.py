#!/usr/bin/env python3

import argparse
import datetime
import json
import logging
from json.decoder import JSONDecodeError
from time import sleep
from typing import Dict
from zoneinfo import ZoneInfo

import daemon
import parsedatetime
import schedule
from requests.exceptions import HTTPError

import shopgoodwill


class BidSniper:
    def __init__(self, config: Dict, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.dry_run_msg = "DRY-RUN: " if dry_run else ""

        # logging setup
        self.logger = logging.getLogger("shopgoodwill_bid_sniper")
        logging.basicConfig()
        logging_conf = config.get("logging", dict())
        self.logger.setLevel(logging_conf.get("log_level", logging.INFO))
        if "gotify" in logging_conf:
            from gotify_handler import GotifyHandler

            self.logger.addHandler(GotifyHandler(**logging_conf["gotify"]))

        # TODO since this is a daemon,
        # we'll actually have to bother refreshing the token!
        self.shopgoodwill_client = shopgoodwill.Shopgoodwill(config["auth_info"])

        # custom time alerting setup
        self.alert_time_deltas = list()
        cal = parsedatetime.Calendar()
        for time_delta_str in config["bid_sniper"].get("alert_time_deltas", list()):
            time_delta = (
                cal.parseDT(time_delta_str, sourceTime=datetime.datetime.min)[0]
                - datetime.datetime.min
            )
            if time_delta != datetime.timedelta(0):
                self.alert_time_deltas.append(time_delta)
            else:
                self.logger.warning("Invalid time delta string "
                                    f"'{time_delta_str}'")

        self.favorites_cache = {
            "last_updated": datetime.datetime.min,
            "favorites": list(),
        }
        self.scheduled_tasks = (
            set()
        )  # Will contain itemIds tentatively scheduled for actions

        # initial kick-off run
        self.main_loop()

        # schedule the main_loop for every X seconds
        # note that schedule does not account for execution time
        schedule.every(
            self.config["bid_sniper"].get("refresh_seconds", 300)
        ).seconds.do(self.main_loop)

        return

    def update_favorites_cache(self, max_cache_time: int) -> None:
        """
        Simple function to update the local favorites cache

        :param max_cache_time: The number of seconds
            after which the cache should be refreshed
        :type max_cache_time: int
        :rtype: None
        """

        if (
            datetime.datetime.now() - self.favorites_cache["last_updated"]
        ).seconds > max_cache_time:
            try:
                self.favorites_cache = {
                    "favorites": self.shopgoodwill_client.get_favorites(),
                    "last_updated": datetime.datetime.now(),
                }

            except HTTPError as he:
                self.logger.error(f"HTTPError updating favorites cache - {he}")
                # TODO do we keep the stale cache, or raise the exception?
                # stale caches can lead to erroneous bids
                #
                # for now we'll do nothing

    def time_alert(self, item_id: int, end_time_str: str) -> schedule.CancelJob:
        """
        Simply logs an alert to remind the user that an auction is ending

        :param item_id: A valid ShopGoodwill item ID
        :type item_id: int
        :param end_time_str: The end time of the auction in ISO-8601
        :type end_time_str: str
        :return: the schedule.CancelJob class, so this only runs once
        :rtype: schedule.CancelJob
        """
        self.update_favorites_cache(
            self.config["bid_sniper"].get("favorites_max_cache_seconds", 60)
        )

        # Check if we still want to alert on this item
        if item_id in self.favorites_cache["favorites"]:
            favorite = self.favorites_cache[item_id]
            self.logger.info(
                f"time alert - {favorite['title']} ending at {end_time_str}")

        return schedule.CancelJob

    def place_bid(self, item_id: int) -> schedule.CancelJob:
        """
        Given an item ID, do the following:
            Check if it's still in our favorites
            If it is, see if we've set a max bid amount for it
            If our max bid amount is greater than the current price,
                place a bid for that amount

        :param item_id: A valid ShopGoodwill item ID
        :type item_id: int
        :return: the schedule.CancelJob class, so this only runs once
        :rtype: schedule.CancelJob
        """

        # we must be _absolutely_ sure that the user still wishes to place a bid -
        # the config could've changed between scheduling and this function call

        # force an update of the favorites cache,
        # as we don't want to place erronous bids
        self.update_favorites_cache(0)

        # find the max_bid amount (if present) for this itemId
        favorite = self.favorites_cache["favorites"].get(item_id, None)
        if not favorite:
            return schedule.CancelJob

        notes = favorite.get("notes", None)
        if not notes:
            return schedule.CancelJob

        try:
            notes_js = json.loads(notes)
        except JSONDecodeError:
            # TODO should this be treated as an error? I don't think so.
            return schedule.CancelJob

        max_bid = notes_js.get("max_bid", None)
        if not max_bid:
            return schedule.CancelJob

        try:
            max_bid = float(max_bid)
        except ValueError:
            self.logger.error(
                f"ValueError casting max_bid value '{max_bid}' as float"
            )
            return schedule.CancelJob

        # we need the sellerId before placing a bid, which is _only_
        # available on the item page
        try:
            item_info = self.shopgoodwill_client.get_item_bid_info(item_id)
        except HTTPError as he:
            self.logger.error(
                "HTTPError getting info for item "
                f"'{item_info['title']}' - {he}"
            )
            return schedule.CancelJob

        # Don't try bidding if we can't win
        if max_bid < item_info["currentPrice"]:
            # TODO tell the user what happened
            self.logger.warning(
                "Bid amount {max_bid} for item '{item['title']}' "
                f"below current price {item_info['currentPrice']}"
            )
            return schedule.CancelJob

        # finally place a bid
        self.logger.info(
            f"{self.dry_run_msg}placing bid on "
            f"'{item_info['title']}' for {max_bid}"
        )
        if not self.dry_run:
            # TODO in the future, address how SGW uses quantity
            # I don't think they use it in auctions
            try:
                self.shopgoodwill_client.place_bid(
                    item_id, max_bid, item_info["sellerId"], quantity=1
                )
            except HTTPError as he:
                self.logger.error(
                    "HTTPError placing bid on "
                    f"'{item_info['title']}' - {he}"
                )
                return schedule.CancelJob

        return schedule.CancelJob

    def main_loop(self) -> None:
        # update favorites
        self.update_favorites_cache(
            self.config["bid_sniper"].get("favorites_max_cache_seconds", 60)
        )

        for item_id, favorite_info in self.favorites_cache["favorites"].items():
            # Don't double-schedule tasks
            if item_id in self.scheduled_tasks:
                continue

            # so close but yet so far away from ISO-8601
            # SGW simply trims the "PDT" (or PST?) off of the timestamps
            # TODO validate that the site only uses a single timezone!
            end_time = (
                datetime.datetime.fromisoformat(favorite_info["endTime"])
                .replace(tzinfo=ZoneInfo("US/Pacific"))
                .astimezone(ZoneInfo("Etc/UTC"))
            )

            # schedule reminders for whenever the user configured
            now = datetime.datetime.utcnow().replace(tzinfo=ZoneInfo("Etc/UTC"))
            for alert_time_delta in self.alert_time_deltas:
                delta_to_event = end_time - alert_time_delta - now

                # skip events in the past
                if delta_to_event.days < 0:
                    continue

                self.logger.debug(
                    "scheduling time alert for item "
                    f"{item_id} in {delta_to_event.seconds} seconds"
                )
                schedule.every(delta_to_event.seconds).seconds.do(
                    self.time_alert,
                    item_id,
                    end_time.isoformat(),
                )

            # schedule a tentative max_bid for this item
            cal = parsedatetime.Calendar()
            time_delta_str = self.config["bid_sniper"].get(
                "bid_snipe_time_delta", "30 seconds"
            )
            time_delta = (
                cal.parseDT(time_delta_str, sourceTime=datetime.datetime.min)[0]
                - datetime.datetime.min
            )
            if time_delta == datetime.timedelta(0):
                self.logger.warning("Invalid time delta string "
                                    f"'{time_delta_str}'")
            else:

                delta_to_event = end_time - time_delta - now
                self.logger.debug(
                    "scheduling bid for item "
                    f"{item_id} in {delta_to_event.seconds} seconds"
                )
                schedule.every(delta_to_event.seconds).seconds.do(
                    self.place_bid,
                    item_id,
                    self.dry_run,
                )

            # mark this item ID as "scheduled"
            self.scheduled_tasks.add(item_id)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to config file - defaults to ./config.json",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="If set, do not perform any actions "
        "that modify state on ShopGoodwill (eg. placing bids)",
    )
    args = parser.parse_args()

    return args


def main():
    args = parse_args()
    with open(args.config, "r") as f:
        config = json.load(f)

    bid_sniper = BidSniper(config, args.dry_run)

    while True:
        schedule.run_pending()
        sleep(1)

if __name__ == "__main__":
    # TODO this doesn't seem to call main()...
    #with daemon.DaemonContext():
    #    main()
    main()