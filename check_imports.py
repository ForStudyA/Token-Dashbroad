"""Quick import check for hermes-token-dash."""
from hermes_token_dash.parser_claude import get_time_cutoff
print("get_time_cutoff:", get_time_cutoff)
print("  all =", get_time_cutoff("all"))
print("  today =", get_time_cutoff("today"))
print("  7d =", get_time_cutoff("7d"))
print("  30d =", get_time_cutoff("30d"))

from hermes_token_dash.server import app
print("server imports OK, routes:", len(app.routes))
