# Shopgoodwill Scripts
A collection of scripts for programmatically interacting with [Shopgoodwill](https://shopgoodwill.com).

## Requirements
* python3
* requests
* gotify-handler (optional - required only if you'd like to log to gotify)

## Configuration Setup
See `config.json.example` for an example configuration file.

### `auth_info`
This section is only needed if you want to use functionality requiring a ShopGoodwill account.

At this time, I've yet to reverse the "encryption" performed by ShopGoodwill for the username/password parameters. I'm not going to describe their process at this time (perhaps it's to come in a future blog post), but note that the `username` and `password` fields in the `auth_info` section of the config need to be "encrypted" by ShopGoodwill, and not the plaintext values.

To find the "encrypted" variants of these parameters, fire up your browser of choice, open the network monitor, and log in to the service. The `POST` request to `https://buyerapi.shopgoodwill.com/api/SignIn/Login` will contain the values that you're looking for.

### `logging`
`log_level` - sets the log level to subscribe to
`gotify` - only required if you wish to use gotify as a logging destination

### `seen_listings_filename`
This is the path of the file that will have "seen" listings written to, so we can track "new" ones. This is used by `alert_on_new_query_results.py`, and should probably be moved elsewhere.

### `saved_queries`
This section contains `{query_friendly_name: query}` JSON objects, for use by `alert_on_new_query_results.py`. `query` should be a query JSON, as described below.

## Scripts
### `alert_on_new_query_results.py`

This script executes an "advanced query" as specified by the user, and logs and results that haven't been seen before. `itemID` is used to track listings. "Seen listings" are tracked globally across all queries, so you should only be alerted once about a given item. However, I've seen ShopGoodwill sometimes re-upload auctions with no changes, except for the `itemID`. Those listings will be considered "new".

#### Arguments
|Name|Type|Description|
|-|-|-|
|`-q`|`query-name`|`str`|The name of the query to execute. This must be present in the data source's list of queries|
|`-l`|`--list-queries`|`bool`|If set, list all queries that can be executed by this data source and exit|
|`-d`|`--data-source`|`str`|Either `local` or `saved_searches`. The former reads query JSONs from the config file's `saved_queries` section. The latter reads from a ShopGoodwill account's "Saved Searches"|

#### Query Generation
The easiest way to generate a query JSON is to make an [Advanced Search](https://shopgoodwill.com/search/advancedsearch) on ShopGoodwill. Simply craft the query you'd like, open the network console, and click the search button. The XHR POST request to `https://buyerapi.shopgoodwill.com/api/Search/ItemListing` contains the JSON that you're looking for.

Alternatively, if you can create one from scratch, if you'd like to guess at the query values. See `config.json.example`'s `saved_queries` section for the required fields. 

Once you have a query, you can insert it into the configuration file under `saved_queries` with a distinctive name.
*Note* - the `page` and `pageSize` attributes in a query will be ignored, and the query will paginate until all results have been accounted for. Additionally, `closedAuctionEndingDate` can be adjusted to an invalid date (eg. 1/1/1), which _should_ cover all of time. Since the search function only returns active listings, there isn't concern of getting stale results.

## Final Notes
It's worth noting that the logic to derive a query JSON from a ShopGoodwill saved search may not be 100% accurate. Thus, I'd recommend using query JSONs in the config file if possible. If you're interested in knowing why I take this view, check out how saved searches actually generate queries in the web UI. It's not straight-forward. Not to take this time to rant, but the API is _dirty_.
