"""Every rule OpenAlgo's app registers, each in one class: the G5 route suite's decisions (P6 7.1).

test_g5_routes.py walks every rule of the real app's url_map (g5_probe.py) and fails on any rule
this table does not name, with "classify this route". That is the point of this file: an upstream
rebase or a SUDARSHAN change that adds a route stops there until someone decides, and the decision
is one line here, which the reviewer reads. Every route-adding PR adds its own line (cross-design
Y15). There is no pattern and no wildcard: a line names one rule by its path and its endpoint, as
``flask routes`` prints them, and a line naming a rule the app no longer has fails too.

The callers: an anonymous visitor, a family phone (only the family cookie), a local process (no
cookie, none of nginx's headers) and, on ``/sudarshan/*``, a session signed with the app's key for
a user who is not the owner. What the suite holds each class to:

``protected``      Refuses every caller: 401, 403, a 302 to the login page, or 400 from the CSRF
                   check on a write; never "status": "success" in the body. Every rule that is not
                   in one of the classes below.
``public_shell``   A React shell view: byte for byte the index.html served at /, which carries no
                   data, a 404 for a page this deployment does not have, or a redirect to another
                   shell view. Never takes a write.
``public_static``  A file or a fixed document with no account data: answers 200, 304 or 404, never
                   takes a write.
``public_auth``    The login flow and what the login page reads: may take a write, and never
                   hands out a logged-in session (nor does any other route, to any caller).
``webhook``        Authenticated by a key or token in the request, not by a session: 400 (a body
                   without the key), 401, 403 or 404 (a token it does not know); never "success".
``internal``       A ring service's sealed hand-off, which answers 404 to anything nginx
                   forwarded: 401, 403 or 404 to every caller; never "success".

No route may raise, open a connection, look up a name, start a process or publish an event other
than a refusal's ``...FailedEvent`` while it answers any of these callers.

A line is ``<class> <rule> <endpoint>``, then ``# <reason>``; a ``## <reason>`` line gives the
reason for the lines below it, up to the next blank line, that have none of their own. Every line
of public_static, public_auth, webhook and internal needs a reason: a route is public because
someone wrote down why. A line's class follows what the route must be, not what it answers today;
a route that refuses every caller, but not cleanly, keeps its class and is listed in FINDINGS.
"""

from __future__ import annotations

from typing import NamedTuple

PROTECTED = "protected"
PUBLIC_SHELL = "public_shell"
PUBLIC_STATIC = "public_static"
PUBLIC_AUTH = "public_auth"
WEBHOOK = "webhook"
INTERNAL = "internal"
CLASSES = (PROTECTED, PUBLIC_SHELL, PUBLIC_STATIC, PUBLIC_AUTH, WEBHOOK, INTERNAL)
#: The classes whose rules may take POST, PUT, PATCH or DELETE.
WRITABLE = frozenset({PROTECTED, PUBLIC_AUTH, WEBHOOK, INTERNAL})
#: The classes whose every line says why.
NEEDS_REASON = frozenset({PUBLIC_STATIC, PUBLIC_AUTH, WEBHOOK, INTERNAL})

TABLE = """
# --- public_shell: the React shell; every view answers the index.html that / answers ---
public_shell  /                                      react.react_index
public_shell  /<broker>/auth                         react.react_broker_auth
public_shell  /about                                 react.react_about
public_shell  /action-center                         react.react_action_center
public_shell  /admin                                 react.react_admin_index
public_shell  /admin/diagnostics                     react.react_admin_diagnostics
public_shell  /admin/freeze                          react.react_admin_freeze
public_shell  /admin/holidays                        react.react_admin_holidays
public_shell  /admin/remote-mcp                      react.react_admin_remote_mcp
public_shell  /admin/timings                         react.react_admin_timings
public_shell  /agent                                 react.react_agent
public_shell  /agent/config                          react.react_agent_config
public_shell  /analyzer                              react.react_analyzer
public_shell  /arbitrage                             react.react_arbitrage
public_shell  /autologin                             react.react_autologin
public_shell  /broker                                react.react_broker
public_shell  /broker/<broker>/totp                  react.react_broker_totp
public_shell  /broker/samco/auth                     react.react_broker_samco_auth
public_shell  /chart                                 react.react_phone_chart
public_shell  /chart/<exchange>/<symbol>             react.react_phone_chart_symbol
public_shell  /chart/test                            react.react_chart_test
public_shell  /chartink                              react.react_chartink_index
public_shell  /chartink/<int:strategy_id>            react.react_chartink_view
public_shell  /chartink/<int:strategy_id>/configure  react.react_chartink_configure
public_shell  /chartink/new                          react.react_chartink_new
public_shell  /dashboard                             react.react_dashboard
public_shell  /download                              react.react_download
public_shell  /error                                 react.react_error
public_shell  /faq                                   react.react_faq
public_shell  /family                                react.react_family_shell
public_shell  /family-members                        react.react_family_members
public_shell  /family/<path:rest>                    react.react_family_shell
public_shell  /flow                                  react.react_flow_index
public_shell  /flow/editor/<int:workflow_id>         react.react_flow_editor
public_shell  /flow/shortcuts                        react.react_flow_shortcuts
public_shell  /gammadensity                          react.react_gammadensity
public_shell  /gex                                   react.react_gex
public_shell  /gocharting                            react.react_gocharting
public_shell  /health                                react.react_health
public_shell  /historify                             react.react_historify
public_shell  /historify/charts                      react.react_historify_charts
public_shell  /historify/charts/<symbol>             react.react_historify_charts_symbol
public_shell  /holdings                              react.react_holdings
public_shell  /ivchart                               react.react_ivchart
public_shell  /ivsmile                               react.react_ivsmile
public_shell  /journal                               react.react_journal
public_shell  /latency                               react.react_latency
public_shell  /leverage                              react.react_leverage
public_shell  /login                                 react.react_login
public_shell  /logs                                  react.react_logs
public_shell  /logs/latency                          react.react_logs_latency
public_shell  /logs/live                             react.react_logs_live
public_shell  /logs/sandbox                          react.react_logs_sandbox
public_shell  /logs/security                         react.react_logs_security
public_shell  /logs/traffic                          react.react_logs_traffic
public_shell  /market-alerts                         react.react_market_alerts
public_shell  /master-contract                       react.react_master_contract
public_shell  /maxpain                               react.react_maxpain
public_shell  /oiprofile                             react.react_oiprofile
public_shell  /oirange                               react.react_oirange
public_shell  /oitracker                             react.react_oitracker
public_shell  /optionchain                           react.react_optionchain
public_shell  /orderbook                             react.react_orderbook
public_shell  /platforms                             react.react_platforms
public_shell  /playground                            react.react_playground
public_shell  /playground/                           playground.index  # the old address: redirects to the shell's /playground
public_shell  /pnl-tracker                           react.react_pnltracker
public_shell  /portfolio                             react.react_portfolio_legacy
public_shell  /portfolio-analyzer                    react.react_portfolio_analyzer
public_shell  /portfolio-backtester                  react.react_portfolio_backtester
public_shell  /portfolio-backtester/results          react.react_portfolio_backtester_results
public_shell  /positions                             react.react_positions
public_shell  /profile                               react.react_profile
public_shell  /python                                react.react_python_index
public_shell  /python/<strategy_id>/edit             react.react_python_edit
public_shell  /python/<strategy_id>/logs             react.react_python_logs
public_shell  /python/<strategy_id>/schedule         react.react_python_schedule
public_shell  /python/guide                          react.react_python_guide
public_shell  /python/new                            react.react_python_new
public_shell  /rate-limited                          react.react_rate_limited
public_shell  /reports                               react.react_reports
public_shell  /reset-password                        react.react_reset_password
public_shell  /risk                                  react.react_risk
public_shell  /sandbox                               react.react_sandbox
public_shell  /sandbox/mypnl                         react.react_sandbox_mypnl
public_shell  /scalping                              react.react_scalping
public_shell  /screener                              react.react_screener
public_shell  /search                                react.react_search
public_shell  /search/token                          react.react_search_token
public_shell  /security                              react.react_security
public_shell  /setup                                 react.react_setup
public_shell  /sip-backtester                        react.react_sip_backtester
public_shell  /sip-backtester/results                react.react_sip_backtester_results
public_shell  /straddle                              react.react_straddle
public_shell  /straddlepnl                           react.react_straddlepnl
public_shell  /strategy                              react.react_strategy_index
public_shell  /strategy/<int:strategy_id>            react.react_strategy_detail
public_shell  /strategy/<int:strategy_id>/edit       react.react_strategy_edit
public_shell  /strategy/new                          react.react_strategy_new
public_shell  /strategybuilder                       react.react_strategybuilder
public_shell  /strategybuilder/portfolio             react.react_strategybuilder_portfolio
public_shell  /telegram                              react.react_telegram_index
public_shell  /telegram/analytics                    react.react_telegram_analytics
public_shell  /telegram/config                       react.react_telegram_config
public_shell  /telegram/users                        react.react_telegram_users
public_shell  /tools                                 react.react_tools
public_shell  /tools/strategy                        react.react_tools_strategy_legacy
public_shell  /tools/strategy/portfolio              react.react_tools_strategy_portfolio_legacy
public_shell  /tradebook                             react.react_tradebook
public_shell  /trading                               react.react_trading
public_shell  /tradingview                           react.react_tradingview
public_shell  /traffic                               react.react_traffic
public_shell  /volsurface                            react.react_volsurface
public_shell  /watchlist                             react.react_watchlist
public_shell  /websocket/order                       react.react_websocket_order
public_shell  /websocket/test                        react.react_websocket_test
public_shell  /websocket/test/20                     react.react_websocket_test_20
public_shell  /websocket/test/30                     react.react_websocket_test_30
public_shell  /websocket/test/50                     react.react_websocket_test_50
public_shell  /whatsapp                              react.react_whatsapp_index

# --- public_static: files and fixed documents with no account data ---
public_static /.well-known/oauth-authorization-server                     mcp_wellknown_bp.discovery_authorization_server         # remote MCP: OAuth server metadata (RFC 8414), read before a client holds a token
public_static /.well-known/oauth-protected-resource                       mcp_wellknown_bp.discovery_protected_resource           # remote MCP: protected-resource metadata (RFC 9728)
public_static /.well-known/oauth-protected-resource/<path:resource_path>  mcp_wellknown_bp.discovery_protected_resource_for_path  # remote MCP: protected-resource metadata (RFC 9728)
public_static /.well-known/oauth-protected-resource/mcp                   mcp_wellknown_bp.discovery_protected_resource_for_path  # remote MCP: protected-resource metadata (RFC 9728)
public_static /api/config/host                                            get_host_config                                         # the address in HOST_SERVER, which the visitor already used; no account data
public_static /api/v1/                                                    api_v1.root                                             # the API's documentation page, switched off (404)
public_static /api/v1/sip/frequencies                                     api_v1.sip_sip_frequencies                              # a fixed list of SIP frequencies; no key and no account data
public_static /api/v1/swagger.json                                        api_v1.specs                                            # the API's own description: paths and fields, no data
public_static /apple-touch-icon.png                                       react.serve_apple_touch_icon                            # the home-screen icon
public_static /assets/<path:filename>                                     react.serve_assets                                      # the built app's script, style and font files
public_static /docs/<path:filename>                                       react.serve_docs                                        # help pages shipped inside the built app
public_static /favicon.ico                                                react.serve_favicon                                     # the browser tab icon
public_static /health/check                                               health_bp.detailed_health_check                         # health probe: each database and the proxy, pass or fail; no account data
public_static /health/status                                              health_bp.simple_health                                 # uptime probe for a monitor (upstream exempts it from CSRF for that); no account data
public_static /images/<path:filename>                                     react.serve_images                                      # images and the web manifest shipped with the app
public_static /logo.png                                                   react.serve_logo                                        # the app's logo
public_static /mcp/.well-known/oauth-protected-resource                   mcp_http_bp.mcp_resource_metadata_alias                 # remote MCP: the same metadata under /mcp
public_static /mcp/healthz                                                mcp_http_bp.healthz                                     # remote MCP: liveness for a monitor; no data
public_static /oauth/jwks.json                                            mcp_oauth_bp.jwks_endpoint                              # remote MCP: the public key its tokens are checked with
public_static /offline.html                                               react.serve_offline_page                                # the family view's offline page, a fixed document from the build; 404 until a build has it (P6 B2)
public_static /sounds/<path:filename>                                     react.serve_sounds                                      # alert sounds shipped with the app
public_static /static/<path:filename>                                     static                                                  # Flask's own static folder; the repository has none, so every file is 404
public_static /sw.js                                                      react.serve_service_worker                              # the family view's service worker script from the build, scope held to /family/; 404 until a build has it (P6 B2)
public_static /swaggerui/<path:filename>                                  restx_doc.static                                        # the files of the API documentation viewer

# --- public_auth: the login flow and what the login page reads ---
public_auth   /auth/app-info                      auth.get_app_info             # the app's name and version, for the login page
public_auth   /auth/broker-config                 auth.get_broker_config        # the broker's name for the login button; its key only to a session
public_auth   /auth/check-setup                   auth.check_setup_required     # tells the login page whether the owner's account exists
public_auth   /auth/csrf-token                    auth.get_csrf_token           # the token the login form posts with; no credential
public_auth   /auth/login                         auth.login                    # the owner's login, password step
public_auth   /auth/login/totp                    auth.login_totp               # the login's second factor
public_auth   /auth/logout                        auth.logout                   # ends the caller's own session; with none, says logged out
public_auth   /auth/reset-password                auth.reset_password           # password reset by email, in steps
public_auth   /auth/reset-password-email/<token>  auth.reset_password_email     # the link in the reset email
public_auth   /auth/session-status                auth.get_session_status       # tells the app whether this browser is logged in
public_auth   /oauth/register                     mcp_oauth_bp.register_client  # remote MCP: open client registration (RFC 7591); the owner still consents at /oauth/authorize

# --- webhook: a key or a token in the request, not a session ---
## the REST API: the API key in the body or query; refused without it
webhook       /api/v1/analyzer/               api_v1.analyzer_analyzer_status
webhook       /api/v1/analyzer/toggle         api_v1.analyzer_analyzer_toggle
webhook       /api/v1/basketorder/            api_v1.basket_order_basket_order
webhook       /api/v1/cancelallorder/         api_v1.cancel_all_order_cancel_all_order
webhook       /api/v1/cancelgttorder/         api_v1.cancel_gtt_order_cancel_gtt_order
webhook       /api/v1/cancelorder/            api_v1.cancel_order_cancel_order
webhook       /api/v1/chart                   api_v1.chart_chart_preferences_resource
webhook       /api/v1/closeposition/          api_v1.close_position_close_position
webhook       /api/v1/depth/                  api_v1.depth_depth
webhook       /api/v1/expiry/                 api_v1.expiry_expiry
webhook       /api/v1/funds/                  api_v1.funds_funds
webhook       /api/v1/gttorderbook/           api_v1.gtt_orderbook_gtt_order_book
webhook       /api/v1/history/                api_v1.history_history
webhook       /api/v1/holdings/               api_v1.holdings_holdings
webhook       /api/v1/instruments/            api_v1.instruments_instruments
webhook       /api/v1/intervals/              api_v1.intervals_intervals
webhook       /api/v1/margin/                 api_v1.margin_margin_calculator
webhook       /api/v1/market/holidays/        api_v1.market/holidays_market_holidays
webhook       /api/v1/market/timings/         api_v1.market/timings_market_timings
webhook       /api/v1/modifygttorder/         api_v1.modify_gtt_order_modify_gtt_order
webhook       /api/v1/modifyorder/            api_v1.modify_order_modify_order
webhook       /api/v1/multioptiongreeks       api_v1.multioptiongreeks_multi_option_greeks
webhook       /api/v1/multiquotes/            api_v1.multiquotes_multi_quotes
webhook       /api/v1/openposition/           api_v1.openposition_open_position
webhook       /api/v1/optionchain/            api_v1.optionchain_option_chain
webhook       /api/v1/optiongreeks            api_v1.optiongreeks_option_greeks
webhook       /api/v1/optionsmultiorder/      api_v1.optionsmultiorder_options_multi_order
webhook       /api/v1/optionsorder/           api_v1.optionsorder_options_order
webhook       /api/v1/optionsymbol/           api_v1.optionsymbol_option_symbol
webhook       /api/v1/orderbook/              api_v1.orderbook_orderbook
webhook       /api/v1/orderstatus/            api_v1.orderstatus_order_status
webhook       /api/v1/ping/                   api_v1.ping_ping
webhook       /api/v1/placegttorder/          api_v1.place_gtt_order_place_gtt_order
webhook       /api/v1/placeorder/             api_v1.place_order_place_order
webhook       /api/v1/placesmartorder/        api_v1.place_smart_order_smart_order
webhook       /api/v1/pnl/symbols             api_v1.pnl_pn_l_symbols
webhook       /api/v1/portfolio/backtest      api_v1.portfolio_portfolio_backtest
webhook       /api/v1/portfolio/benchmarks    api_v1.portfolio_portfolio_benchmarks
webhook       /api/v1/portfolio/holdings      api_v1.portfolio_portfolio_holdings
webhook       /api/v1/portfolio/tearsheet     api_v1.portfolio_portfolio_tearsheet
webhook       /api/v1/positionbook/           api_v1.positionbook_positionbook
webhook       /api/v1/quotes/                 api_v1.quotes_quotes
webhook       /api/v1/search/                 api_v1.search_search
webhook       /api/v1/sip/backtest            api_v1.sip_sip_backtest
webhook       /api/v1/splitorder/             api_v1.split_order_split_order
webhook       /api/v1/strategy/close_all      api_v1.strategy_strategy_close_all
webhook       /api/v1/strategy/close_leg      api_v1.strategy_strategy_close_leg
webhook       /api/v1/strategy/events         api_v1.strategy_strategy_events
webhook       /api/v1/strategy/list           api_v1.strategy_strategy_list
webhook       /api/v1/strategy/orders         api_v1.strategy_strategy_orders
webhook       /api/v1/strategy/runs           api_v1.strategy_strategy_runs
webhook       /api/v1/strategy/start          api_v1.strategy_strategy_start
webhook       /api/v1/strategy/status         api_v1.strategy_strategy_status
webhook       /api/v1/strategy/stop           api_v1.strategy_strategy_stop
webhook       /api/v1/symbol/                 api_v1.symbol_symbol
webhook       /api/v1/syntheticfuture/        api_v1.syntheticfuture_synthetic_future
webhook       /api/v1/telegram/broadcast      api_v1.telegram_broadcast_message
webhook       /api/v1/telegram/config         api_v1.telegram_telegram_bot_config
webhook       /api/v1/telegram/notify         api_v1.telegram_send_notification
webhook       /api/v1/telegram/notify-file    api_v1.telegram_send_file_notification
webhook       /api/v1/telegram/preferences    api_v1.telegram_user_preferences
webhook       /api/v1/telegram/start          api_v1.telegram_start_bot
webhook       /api/v1/telegram/stats          api_v1.telegram_telegram_stats
webhook       /api/v1/telegram/stop           api_v1.telegram_stop_bot
webhook       /api/v1/telegram/users          api_v1.telegram_telegram_users
webhook       /api/v1/ticker/<string:symbol>  api_v1.ticker_ticker
webhook       /api/v1/tradebook/              api_v1.tradebook_tradebook
webhook       /api/v1/whatsapp/notify         api_v1.whatsapp_whats_app_notify

webhook       /api/v1/telegram/webhook        api_v1.telegram_webhook_handler               # Telegram's calls: the secret header it sends (401 without, once a bot or secret is set)
webhook       /chartink/webhook/<webhook_id>  chartink_bp.webhook                           # Chartink alerts: the webhook id in the path
webhook       /flow/webhook/<token>           flow.trigger_webhook                          # Flow webhooks: the token in the path
webhook       /flow/webhook/<token>/<symbol>  flow.trigger_webhook_with_symbol              # Flow webhooks: the token in the path
webhook       /mcp                            mcp_http_bp.mcp_dispatch                      # remote MCP calls: an OAuth bearer token
webhook       /mcp                            mcp_http_bp.mcp_preflight                     # remote MCP CORS preflight: OPTIONS only, nothing to walk
webhook       /mcp                            mcp_http_bp.mcp_sse                           # remote MCP stream: an OAuth bearer token
webhook       /oauth/revoke                   mcp_oauth_bp.revoke_endpoint                  # remote MCP: client credentials
webhook       /oauth/token                    mcp_oauth_bp.token_endpoint                   # remote MCP: client credentials and PKCE
webhook       /postback/<broker>              postback_bp.broker_postback                   # broker order postbacks: matched to the live broker session
webhook       /strategy/webhook/<token>       strategy_module_bp.webhook                    # strategy alerts (TradingView): the token in the path
webhook       /sudarshan/risk/api/killswitch  sudarshan_risk_bp.api_turn_on                 # kill switch for outside platforms: the API key in the body (M5.13)

# --- internal: ring services' sealed hand-offs, called on 127.0.0.1 ---
internal      /sudarshan/alerts/internal/push          sudarshan_alerts.internal_push                  # sudarshan-alerts pushes events: sealed, 404 through nginx
internal      /sudarshan/autologin/internal/preflight  sudarshan_autologin.internal_preflight          # the auto-login worker asks before a login: sealed, 404 through nginx
internal      /sudarshan/autologin/internal/report     sudarshan_autologin.internal_report             # the auto-login worker hands over the token: sealed, 404 through nginx
internal      /sudarshan/family/internal/watchlists    sudarshan_family.internal_watchlists            # sudarshan-family reads the lists the owner shares: sealed, 404 through nginx (P6 B1)
internal      /sudarshan/screener/internal/push        sudarshan_screener_internal.internal_push       # sudarshan-screener pushes results: sealed, 404 through nginx
internal      /sudarshan/screener/internal/watchlist   sudarshan_screener_internal.internal_watchlist  # sudarshan-screener reads and fills a watchlist: sealed, 404 through nginx
internal      /sudarshan/watchlists/internal/read      sudarshan_watchlists_internal.read              # sudarshan-alerts reads a list's members: sealed, 404 through nginx

# --- protected: the owner's session only ---
protected     /<broker>/callback                                                        brlogin.broker_callback
protected     /action-center                                                            orders_bp.action_center
protected     /action-center/api/data                                                   orders_bp.action_center_api_data
protected     /action-center/approve-all                                                orders_bp.approve_all_pending_orders
protected     /action-center/approve/<int:order_id>                                     orders_bp.approve_pending_order_route
protected     /action-center/count                                                      orders_bp.action_center_count
protected     /action-center/delete/<int:order_id>                                      orders_bp.delete_pending_order_route
protected     /action-center/reject/<int:order_id>                                      orders_bp.reject_pending_order_route
protected     /admin/api/errors                                                         admin_bp.api_errors_list
protected     /admin/api/errors/client                                                  admin_bp.api_errors_client_report
protected     /admin/api/errors/groups                                                  admin_bp.api_errors_groups
protected     /admin/api/errors/stats                                                   admin_bp.api_errors_stats
protected     /admin/api/freeze                                                         admin_bp.api_freeze_add
protected     /admin/api/freeze                                                         admin_bp.api_freeze_list
protected     /admin/api/freeze/<int:id>                                                admin_bp.api_freeze_delete
protected     /admin/api/freeze/<int:id>                                                admin_bp.api_freeze_edit
protected     /admin/api/freeze/upload                                                  admin_bp.api_freeze_upload
protected     /admin/api/holidays                                                       admin_bp.api_holiday_add
protected     /admin/api/holidays                                                       admin_bp.api_holidays_list
protected     /admin/api/holidays/<int:id>                                              admin_bp.api_holiday_delete
protected     /admin/api/mcp/audit                                                      admin_bp.api_mcp_audit
protected     /admin/api/mcp/kill-switch                                                admin_bp.api_mcp_kill_switch
protected     /admin/api/mcp/settings                                                   admin_bp.api_mcp_settings_get
protected     /admin/api/mcp/settings                                                   admin_bp.api_mcp_settings_put
protected     /admin/api/oauth/clients                                                  admin_bp.api_oauth_clients_list
protected     /admin/api/oauth/clients/<client_id>/approve                              admin_bp.api_oauth_client_approve
protected     /admin/api/oauth/clients/<client_id>/revoke                               admin_bp.api_oauth_client_revoke
protected     /admin/api/stats                                                          admin_bp.api_stats
protected     /admin/api/system                                                         admin_bp.api_system_info
protected     /admin/api/system/diagnostics                                             admin_bp.api_system_diagnostics
protected     /admin/api/system/report                                                  admin_bp.api_system_report
protected     /admin/api/timings                                                        admin_bp.api_timings_list
protected     /admin/api/timings/<exchange>                                             admin_bp.api_timings_edit
protected     /admin/api/timings/check                                                  admin_bp.api_timings_check
protected     /agent/api/catalog/models                                                 agent_bp.catalog_models
protected     /agent/api/catalog/providers                                              agent_bp.catalog_providers
protected     /agent/api/chat/<run_id>/cancel                                           agent_bp.cancel_run
protected     /agent/api/chat/confirm                                                   agent_bp.chat_confirm
protected     /agent/api/chat/stream                                                    agent_bp.chat_stream
protected     /agent/api/chatgpt/cancel                                                 agent_bp.chatgpt_cancel
protected     /agent/api/chatgpt/login                                                  agent_bp.chatgpt_login
protected     /agent/api/chatgpt/session                                                agent_bp.chatgpt_forget
protected     /agent/api/chatgpt/status                                                 agent_bp.chatgpt_status
protected     /agent/api/conversations                                                  agent_bp.create_conversation
protected     /agent/api/conversations                                                  agent_bp.list_conversations
protected     /agent/api/conversations/<int:conversation_id>                            agent_bp.delete_conversation
protected     /agent/api/conversations/<int:conversation_id>                            agent_bp.get_conversation
protected     /agent/api/conversations/<int:conversation_id>/messages/<int:message_id>  agent_bp.truncate_conversation
protected     /agent/api/mcp                                                            agent_bp.get_mcp_servers
protected     /agent/api/mcp/test                                                       agent_bp.test_mcp_servers
protected     /agent/api/models                                                         agent_bp.create_model
protected     /agent/api/models                                                         agent_bp.list_models
protected     /agent/api/models/<int:model_id>                                          agent_bp.delete_model
protected     /agent/api/models/<int:model_id>                                          agent_bp.update_model
protected     /agent/api/models/<int:model_id>/default                                  agent_bp.set_default_model
protected     /agent/api/models/<int:model_id>/test                                     agent_bp.test_model
protected     /agent/api/settings                                                       agent_bp.get_settings
protected     /agent/api/settings                                                       agent_bp.put_settings
protected     /agent/api/status                                                         agent_bp.agent_status
protected     /agent/api/voice                                                          agent_bp.get_voice
protected     /agent/api/voice                                                          agent_bp.put_voice
protected     /agent/api/voice/approve                                                  agent_bp.post_voice_approve
protected     /agent/api/voice/key                                                      agent_bp.delete_voice_key
protected     /agent/api/voice/key                                                      agent_bp.put_voice_key
protected     /agent/api/voice/session                                                  agent_bp.post_voice_session
protected     /agent/api/voice/test                                                     agent_bp.test_voice
protected     /agent/api/voice/transcript                                               agent_bp.post_voice_transcript
protected     /agent/api/websearch                                                      agent_bp.get_websearch
protected     /agent/api/websearch                                                      agent_bp.put_websearch
protected     /agent/api/websearch/providers/<provider>/key                             agent_bp.delete_websearch_key
protected     /agent/api/websearch/providers/<provider>/key                             agent_bp.put_websearch_key
protected     /agent/api/websearch/providers/<provider>/test                            agent_bp.test_websearch_provider
protected     /alerts/fired                                                             alerts_bp.fired
protected     /alerts/log                                                               alerts_bp.clear_log
protected     /alerts/log                                                               alerts_bp.log
protected     /analyzer/                                                                analyzer_bp.analyzer
protected     /analyzer/api/data                                                        analyzer_bp.api_get_data
protected     /analyzer/clear                                                           analyzer_bp.clear_logs
protected     /analyzer/export                                                          analyzer_bp.export_requests
protected     /analyzer/requests                                                        analyzer_bp.get_requests
protected     /analyzer/stats                                                           analyzer_bp.get_stats
protected     /api/broker/capabilities                                                  broker_credentials_bp.get_capabilities
protected     /api/broker/credentials                                                   broker_credentials_bp.get_credentials
protected     /api/broker/credentials                                                   broker_credentials_bp.update_credentials
protected     /api/cache/clear                                                          master_contract_status_bp.clear_cache
protected     /api/cache/health                                                         master_contract_status_bp.get_cache_health
protected     /api/cache/reload                                                         master_contract_status_bp.reload_cache
protected     /api/cache/status                                                         master_contract_status_bp.get_cache_status
protected     /api/master-contract/download                                             master_contract_status_bp.force_master_contract_download
protected     /api/master-contract/ready                                                master_contract_status_bp.check_master_contract_ready
protected     /api/master-contract/smart-status                                         master_contract_status_bp.get_smart_download_status
protected     /api/master-contract/status                                               master_contract_status_bp.get_master_contract_status
protected     /api/strategy-portfolio                                                   strategy_portfolio_bp.create_strategy
protected     /api/strategy-portfolio                                                   strategy_portfolio_bp.list_strategies
protected     /api/strategy-portfolio/<int:entry_id>                                    strategy_portfolio_bp.delete_strategy
protected     /api/strategy-portfolio/<int:entry_id>                                    strategy_portfolio_bp.get_strategy
protected     /api/strategy-portfolio/<int:entry_id>                                    strategy_portfolio_bp.update_strategy
protected     /api/system/permissions                                                   system_permissions_bp.get_permissions
protected     /api/system/permissions/fix                                               system_permissions_bp.fix_permissions
protected     /api/websocket/apikey                                                     websocket.api_get_websocket_apikey
protected     /api/websocket/config                                                     websocket.api_get_websocket_config
protected     /api/websocket/health                                                     websocket.api_websocket_health
protected     /api/websocket/market-data                                                websocket.api_websocket_market_data
protected     /api/websocket/metrics                                                    websocket.api_websocket_metrics
protected     /api/websocket/status                                                     websocket.api_websocket_status
protected     /api/websocket/subscribe                                                  websocket.api_websocket_subscribe
protected     /api/websocket/subscriptions                                              websocket.api_websocket_subscriptions
protected     /api/websocket/trade-safe                                                 websocket.api_trade_management_safe
protected     /api/websocket/unsubscribe                                                websocket.api_websocket_unsubscribe
protected     /api/websocket/unsubscribe-all                                            websocket.api_websocket_unsubscribe_all
protected     /apikey                                                                   api_key_bp.manage_api_key
protected     /apikey/mode                                                              api_key_bp.update_api_key_mode
protected     /arbitrage/api/universe                                                   arbitrage_bp.arbitrage_universe
protected     /auth/2fa/configure                                                       auth.two_factor_configure
protected     /auth/2fa/status                                                          auth.two_factor_status
protected     /auth/active-sessions                                                     auth.active_sessions
protected     /auth/analyzer-mode                                                       auth.get_analyzer_mode_status
protected     /auth/analyzer-toggle                                                     auth.toggle_analyzer_mode_session
protected     /auth/broker                                                              auth.broker_login
protected     /auth/change                                                              auth.change_password
protected     /auth/change-password                                                     auth.change_password_api
protected     /auth/dashboard-data                                                      auth.get_dashboard_data
protected     /auth/debug-smtp                                                          auth.debug_smtp
protected     /auth/profile-data                                                        auth.get_profile_data
protected     /auth/smtp-config                                                         auth.configure_smtp
protected     /auth/test-smtp                                                           auth.test_smtp
protected     /cancel_all_orders                                                        orders_bp.cancel_all_orders_ui
protected     /cancel_gtt_order                                                         orders_bp.cancel_gtt_order_ui
protected     /cancel_order                                                             orders_bp.cancel_order_ui
protected     /chart/test/api/history                                                   chart_test_bp.chart_test_history
protected     /chartink/                                                                chartink_bp.index
protected     /chartink/<int:strategy_id>                                               chartink_bp.view_strategy
protected     /chartink/<int:strategy_id>/configure                                     chartink_bp.configure_symbols
protected     /chartink/<int:strategy_id>/delete                                        chartink_bp.delete_strategy_route
protected     /chartink/<int:strategy_id>/symbol/<int:mapping_id>/delete                chartink_bp.delete_symbol
protected     /chartink/<int:strategy_id>/toggle                                        chartink_bp.toggle_strategy_route
protected     /chartink/api/strategies                                                  chartink_bp.api_get_strategies
protected     /chartink/api/strategy                                                    chartink_bp.api_create_strategy
protected     /chartink/api/strategy/<int:strategy_id>                                  chartink_bp.api_get_strategy
protected     /chartink/api/strategy/<int:strategy_id>/toggle                           chartink_bp.api_toggle_strategy
protected     /chartink/new                                                             chartink_bp.new_strategy
protected     /chartink/search                                                          chartink_bp.search_symbols
protected     /close_all_positions                                                      orders_bp.close_all_positions
protected     /close_position                                                           orders_bp.close_position
protected     /custom-indicators/<path:filename>                                        custom_indicators_bp.module
protected     /custom-indicators/index.json                                             custom_indicators_bp.index
protected     /dashboard                                                                dashboard_bp.dashboard
protected     /dhan/initiate-oauth                                                      brlogin.dhan_initiate_oauth
protected     /flow/api/index-symbols                                                   flow.get_index_symbols_lot_sizes
protected     /flow/api/monitor/status                                                  flow.get_monitor_status
protected     /flow/api/option-strikes                                                  flow.get_option_strikes
protected     /flow/api/symbol-lotsizes                                                 flow.get_symbol_lot_sizes
protected     /flow/api/workflows                                                       flow.create_workflow
protected     /flow/api/workflows                                                       flow.list_workflows
protected     /flow/api/workflows/<int:workflow_id>                                     flow.delete_workflow
protected     /flow/api/workflows/<int:workflow_id>                                     flow.get_workflow
protected     /flow/api/workflows/<int:workflow_id>                                     flow.update_workflow
protected     /flow/api/workflows/<int:workflow_id>/activate                            flow.activate_workflow
protected     /flow/api/workflows/<int:workflow_id>/deactivate                          flow.deactivate_workflow
protected     /flow/api/workflows/<int:workflow_id>/execute                             flow.execute_workflow_now
protected     /flow/api/workflows/<int:workflow_id>/executions                          flow.get_workflow_executions
protected     /flow/api/workflows/<int:workflow_id>/export                              flow.export_workflow
protected     /flow/api/workflows/<int:workflow_id>/replace                             flow.replace_workflow
protected     /flow/api/workflows/<int:workflow_id>/webhook                             flow.get_webhook_info
protected     /flow/api/workflows/<int:workflow_id>/webhook/auth-type                   flow.set_webhook_auth
protected     /flow/api/workflows/<int:workflow_id>/webhook/disable                     flow.disable_webhook
protected     /flow/api/workflows/<int:workflow_id>/webhook/enable                      flow.enable_webhook
protected     /flow/api/workflows/<int:workflow_id>/webhook/regenerate                  flow.regenerate_webhook
protected     /flow/api/workflows/<int:workflow_id>/webhook/regenerate-secret           flow.regenerate_webhook_secret_route
protected     /flow/api/workflows/import                                                flow.import_workflow
protected     /gammadensity/api/gamma-data                                              gamma_density_bp.gamma_data
protected     /gex/api/gex-data                                                         gex_bp.gex_data
protected     /gocharting/                                                              gc_json_bp.gocharting_json
protected     /health/api/alerts                                                        health_bp.get_alerts
protected     /health/api/alerts/<int:alert_id>/acknowledge                             health_bp.acknowledge_alert
protected     /health/api/alerts/<int:alert_id>/resolve                                 health_bp.resolve_alert
protected     /health/api/current                                                       health_bp.get_current_metrics
protected     /health/api/history                                                       health_bp.get_metrics_history
protected     /health/api/stats                                                         health_bp.get_health_stats
protected     /health/export                                                            health_bp.export_metrics
protected     /historify/api/catalog                                                    historify_bp.get_catalog
protected     /historify/api/catalog/grouped                                            historify_bp.get_catalog_grouped
protected     /historify/api/catalog/metadata                                           historify_bp.get_catalog_with_metadata
protected     /historify/api/data                                                       historify_bp.get_chart_data
protected     /historify/api/delete                                                     historify_bp.delete_data
protected     /historify/api/delete/bulk                                                historify_bp.bulk_delete_data
protected     /historify/api/download                                                   historify_bp.download_data
protected     /historify/api/download/watchlist                                         historify_bp.download_watchlist
protected     /historify/api/exchanges                                                  historify_bp.get_exchanges
protected     /historify/api/export                                                     historify_bp.export_data
protected     /historify/api/export/bulk                                                historify_bp.bulk_export
protected     /historify/api/export/bulk/download                                       historify_bp.download_bulk_export
protected     /historify/api/export/download                                            historify_bp.download_export
protected     /historify/api/export/preview                                             historify_bp.get_export_preview
protected     /historify/api/fno/chain                                                  historify_bp.get_fno_chain
protected     /historify/api/fno/expiries                                               historify_bp.get_fno_expiries
protected     /historify/api/fno/futures                                                historify_bp.get_futures_chain
protected     /historify/api/fno/options                                                historify_bp.get_option_chain
protected     /historify/api/fno/underlyings                                            historify_bp.get_fno_underlyings
protected     /historify/api/historify-intervals                                        historify_bp.get_historify_intervals
protected     /historify/api/intervals                                                  historify_bp.get_intervals
protected     /historify/api/jobs                                                       historify_bp.create_job
protected     /historify/api/jobs                                                       historify_bp.get_jobs
protected     /historify/api/jobs/<job_id>                                              historify_bp.delete_job
protected     /historify/api/jobs/<job_id>                                              historify_bp.get_job_status
protected     /historify/api/jobs/<job_id>/cancel                                       historify_bp.cancel_job
protected     /historify/api/jobs/<job_id>/pause                                        historify_bp.pause_job
protected     /historify/api/jobs/<job_id>/resume                                       historify_bp.resume_job_endpoint
protected     /historify/api/jobs/<job_id>/retry                                        historify_bp.retry_job
protected     /historify/api/metadata/enrich                                            historify_bp.enrich_metadata
protected     /historify/api/sample/<format_type>                                       historify_bp.download_sample
protected     /historify/api/schedules                                                  historify_bp.create_schedule
protected     /historify/api/schedules                                                  historify_bp.get_schedules
protected     /historify/api/schedules/<schedule_id>                                    historify_bp.delete_schedule
protected     /historify/api/schedules/<schedule_id>                                    historify_bp.get_schedule
protected     /historify/api/schedules/<schedule_id>                                    historify_bp.update_schedule
protected     /historify/api/schedules/<schedule_id>/disable                            historify_bp.disable_schedule
protected     /historify/api/schedules/<schedule_id>/enable                             historify_bp.enable_schedule
protected     /historify/api/schedules/<schedule_id>/executions                         historify_bp.get_schedule_executions
protected     /historify/api/schedules/<schedule_id>/pause                              historify_bp.pause_schedule
protected     /historify/api/schedules/<schedule_id>/resume                             historify_bp.resume_schedule
protected     /historify/api/schedules/<schedule_id>/trigger                            historify_bp.trigger_schedule
protected     /historify/api/stats                                                      historify_bp.get_stats
protected     /historify/api/symbol-info                                                historify_bp.get_symbol_info
protected     /historify/api/upload                                                     historify_bp.upload_data
protected     /historify/api/watchlist                                                  historify_bp.add_watchlist
protected     /historify/api/watchlist                                                  historify_bp.get_watchlist
protected     /historify/api/watchlist                                                  historify_bp.remove_watchlist
protected     /historify/api/watchlist/bulk                                             historify_bp.bulk_add_watchlist
protected     /historify/api/watchlist/bulk/delete                                      historify_bp.bulk_remove_watchlist
protected     /holdings                                                                 orders_bp.holdings
protected     /ivchart/api/default-symbols                                              ivchart_bp.default_symbols
protected     /ivchart/api/intervals                                                    ivchart_bp.intervals
protected     /ivchart/api/iv-data                                                      ivchart_bp.iv_data
protected     /ivsmile/api/iv-smile-data                                                ivsmile_bp.iv_smile_data
protected     /latency/                                                                 latency_bp.latency_dashboard
protected     /latency/api/broker/<broker>/stats                                        latency_bp.get_broker_stats
protected     /latency/api/logs                                                         latency_bp.get_logs
protected     /latency/api/stats                                                        latency_bp.get_stats
protected     /latency/export                                                           latency_bp.export_logs
protected     /leverage/api/current                                                     leverage_bp.get_current
protected     /leverage/api/update                                                      leverage_bp.update_leverage
protected     /logging/                                                                 logging_bp.logging_dashboard
protected     /logs/                                                                    log_bp.view_logs
protected     /logs/export                                                              log_bp.export_logs
protected     /modify_gtt_order                                                         orders_bp.modify_gtt_order_ui
protected     /modify_order                                                             orders_bp.modify_order_ui
protected     /nubra/ip-status                                                          brlogin.nubra_ip_status
protected     /oauth/authorize                                                          mcp_oauth_bp.authorize_endpoint
protected     /oiprofile/api/intervals                                                  oiprofile_bp.intervals
protected     /oiprofile/api/profile-data                                               oiprofile_bp.profile_data
protected     /oitracker/api/maxpain                                                    oitracker_bp.maxpain
protected     /oitracker/api/oi-data                                                    oitracker_bp.oi_data
protected     /openscript/<filename>                                                    openscript_bp.remove
protected     /openscript/<filename>                                                    openscript_bp.save
protected     /openscript/<filename>                                                    openscript_bp.source
protected     /openscript/index.json                                                    openscript_bp.index
protected     /openscript/instrument                                                    openscript_bp.instrument
protected     /openscript/program/<filename>                                            openscript_bp.program
protected     /openscript/runner/close/<path:filename>                                  openscript_runner_bp.close
protected     /openscript/runner/config                                                 openscript_runner_bp.list_settings
protected     /openscript/runner/config/<path:filename>                                 openscript_runner_bp.clear_settings
protected     /openscript/runner/config/<path:filename>                                 openscript_runner_bp.get_settings
protected     /openscript/runner/config/<path:filename>                                 openscript_runner_bp.set_settings
protected     /openscript/runner/instruments                                            openscript_runner_bp.instruments
protected     /openscript/runner/intervals                                              openscript_runner_bp.intervals
protected     /openscript/runner/orderbook/<filename>                                   openscript_runner_bp.orderbook
protected     /openscript/runner/pause/<path:filename>                                  openscript_runner_bp.pause
protected     /openscript/runner/positions/<filename>                                   openscript_runner_bp.positions
protected     /openscript/runner/schedule/<path:filename>                               openscript_runner_bp.clear_schedule
protected     /openscript/runner/schedule/<path:filename>                               openscript_runner_bp.set_schedule
protected     /openscript/runner/start/<path:filename>                                  openscript_runner_bp.start
protected     /openscript/runner/status                                                 openscript_runner_bp.status
protected     /openscript/runner/status/<path:filename>                                 openscript_runner_bp.status
protected     /openscript/runner/stop/<path:filename>                                   openscript_runner_bp.pause
protected     /openscript/runner/tradebook/<filename>                                   openscript_runner_bp.tradebook
protected     /orderbook                                                                orders_bp.orderbook
protected     /orderbook/export                                                         orders_bp.export_orderbook
protected     /platforms/                                                               platforms_bp.index
protected     /playground/api-key                                                       playground.get_api_key
protected     /playground/collections                                                   playground.get_collections
protected     /playground/endpoints                                                     playground.get_endpoints
protected     /pnltracker/api/pnl                                                       pnltracker_bp.get_pnl_data
protected     /pnltracker/legacy                                                        pnltracker_bp.pnltracker
protected     /positions                                                                orders_bp.positions
protected     /positions/export                                                         orders_bp.export_positions
protected     /python/                                                                  python_strategy_bp.index
protected     /python/api/events                                                        python_strategy_bp.api_strategy_events
protected     /python/api/exchanges                                                     python_strategy_bp.api_get_exchanges
protected     /python/api/logs/<strategy_id>                                            python_strategy_bp.api_get_log_files
protected     /python/api/logs/<strategy_id>/<log_name>                                 python_strategy_bp.api_get_log_content
protected     /python/api/strategies                                                    python_strategy_bp.api_get_strategies
protected     /python/api/strategy/<strategy_id>                                        python_strategy_bp.api_get_strategy
protected     /python/api/strategy/<strategy_id>/content                                python_strategy_bp.api_get_strategy_content
protected     /python/check-contracts                                                   python_strategy_bp.check_contracts
protected     /python/clear-error/<strategy_id>                                         python_strategy_bp.clear_error_state
protected     /python/delete/<strategy_id>                                              python_strategy_bp.delete_strategy
protected     /python/edit/<strategy_id>                                                python_strategy_bp.edit_strategy
protected     /python/export/<strategy_id>                                              python_strategy_bp.export_strategy
protected     /python/logs/<strategy_id>                                                python_strategy_bp.view_logs
protected     /python/logs/<strategy_id>/clear                                          python_strategy_bp.clear_logs
protected     /python/new                                                               python_strategy_bp.new_strategy
protected     /python/save/<strategy_id>                                                python_strategy_bp.save_strategy
protected     /python/schedule/<strategy_id>                                            python_strategy_bp.schedule_strategy_route
protected     /python/start/<strategy_id>                                               python_strategy_bp.start_strategy
protected     /python/status                                                            python_strategy_bp.status
protected     /python/stop/<strategy_id>                                                python_strategy_bp.stop_strategy
protected     /python/unschedule/<strategy_id>                                          python_strategy_bp.unschedule_strategy_route
protected     /samco/ip-status                                                          brlogin.samco_ip_status
protected     /sandbox/                                                                 sandbox_bp.sandbox_config
protected     /sandbox/api/configs                                                      sandbox_bp.api_get_configs
protected     /sandbox/mypnl                                                            sandbox_bp.my_pnl
protected     /sandbox/mypnl/api/data                                                   sandbox_bp.api_my_pnl_data
protected     /sandbox/mypnl/export/daily                                               sandbox_bp.export_daily_pnl
protected     /sandbox/mypnl/export/holdings                                            sandbox_bp.export_holdings
protected     /sandbox/mypnl/export/positions                                           sandbox_bp.export_positions
protected     /sandbox/mypnl/export/trades                                              sandbox_bp.export_trades
protected     /sandbox/reload-squareoff                                                 sandbox_bp.reload_squareoff
protected     /sandbox/reset                                                            sandbox_bp.reset_config
protected     /sandbox/squareoff-status                                                 sandbox_bp.squareoff_status
protected     /sandbox/update                                                           sandbox_bp.update_config
protected     /scalping/api/all_underlyings                                             scalping_bp.all_underlyings
protected     /scalping/api/cancel_all                                                  scalping_bp.cancel_all
protected     /scalping/api/close_all                                                   scalping_bp.close_all
protected     /scalping/api/close_leg                                                   scalping_bp.close_leg
protected     /scalping/api/expiry                                                      scalping_bp.expiry
protected     /scalping/api/futures                                                     scalping_bp.futures
protected     /scalping/api/history                                                     scalping_bp.chart_history
protected     /scalping/api/order                                                       scalping_bp.order
protected     /scalping/api/search                                                      scalping_bp.search
protected     /scalping/api/sl                                                          scalping_bp.delete_sl
protected     /scalping/api/sl                                                          scalping_bp.get_sl_states
protected     /scalping/api/sl                                                          scalping_bp.upsert_sl
protected     /scalping/api/strikes                                                     scalping_bp.strikes
protected     /scalping/api/tracked                                                     scalping_bp.reset_tracked
protected     /scalping/api/tracked                                                     scalping_bp.tracked
protected     /scalping/api/underlyings                                                 scalping_bp.underlyings
protected     /search/                                                                  search_bp.search
protected     /search/api/expiries                                                      search_bp.api_expiries
protected     /search/api/search                                                        search_bp.api_search
protected     /search/api/underlyings                                                   search_bp.api_underlyings
protected     /search/token                                                             search_bp.token
protected     /security/                                                                security_bp.security_dashboard
protected     /security/api/active-sessions                                             security_bp.active_sessions_list
protected     /security/api/data                                                        security_bp.security_data
protected     /security/api/login-activity                                              security_bp.login_activity
protected     /security/api/login-activity/clear                                        security_bp.clear_login_activity
protected     /security/ban                                                             security_bp.ban_ip
protected     /security/ban-host                                                        security_bp.ban_host
protected     /security/clear-404                                                       security_bp.clear_404_tracker
protected     /security/settings                                                        security_bp.update_security_settings
protected     /security/stats                                                           security_bp.security_stats
protected     /security/unban                                                           security_bp.unban_ip
protected     /settings/analyze-mode                                                    settings_bp.get_mode
protected     /settings/analyze-mode/<int:mode>                                         settings_bp.set_mode
protected     /setup                                                                    core_bp.setup
protected     /straddle/api/intervals                                                   straddle_bp.straddle_intervals
protected     /straddle/api/straddle-data                                               straddle_bp.straddle_data
protected     /straddlepnl/api/intervals                                                custom_straddle_bp.custom_straddle_intervals
protected     /straddlepnl/api/lotsize                                                  custom_straddle_bp.get_lotsize
protected     /straddlepnl/api/simulate                                                 custom_straddle_bp.simulate
protected     /strategy/api/strategies                                                  strategy_module_bp.create_strategy
protected     /strategy/api/strategies                                                  strategy_module_bp.list_strategies
protected     /strategy/api/strategies/<int:sid>                                        strategy_module_bp.delete_strategy
protected     /strategy/api/strategies/<int:sid>                                        strategy_module_bp.get_strategy_detail
protected     /strategy/api/strategies/<int:sid>                                        strategy_module_bp.update_strategy
protected     /strategy/api/strategies/<int:sid>/checkpoints                            strategy_module_bp.list_checkpoints
protected     /strategy/api/strategies/<int:sid>/close_all                              strategy_module_bp.close_all
protected     /strategy/api/strategies/<int:sid>/events                                 strategy_module_bp.list_events
protected     /strategy/api/strategies/<int:sid>/kill_switch                            strategy_module_bp.engage_kill_switch
protected     /strategy/api/strategies/<int:sid>/legs/<leg_id>/close                    strategy_module_bp.close_one_leg
protected     /strategy/api/strategies/<int:sid>/live                                   strategy_module_bp.set_live
protected     /strategy/api/strategies/<int:sid>/orderbook                              strategy_module_bp.strategy_orderbook
protected     /strategy/api/strategies/<int:sid>/orders                                 strategy_module_bp.list_orders
protected     /strategy/api/strategies/<int:sid>/positions                              strategy_module_bp.strategy_positions
protected     /strategy/api/strategies/<int:sid>/runs                                   strategy_module_bp.list_runs
protected     /strategy/api/strategies/<int:sid>/start                                  strategy_module_bp.start_strategy
protected     /strategy/api/strategies/<int:sid>/stop                                   strategy_module_bp.stop_strategy
protected     /strategy/api/strategies/<int:sid>/tradebook                              strategy_module_bp.strategy_tradebook
protected     /strategy/api/strategies/<int:sid>/unlock_webhook                         strategy_module_bp.release_kill_switch
protected     /strategy/api/strategies/<int:sid>/webhook/rotate                         strategy_module_bp.rotate_webhook
protected     /strategy/api/strategies/<int:sid>/webhook_events                         strategy_module_bp.list_webhook_events
protected     /strategybuilder/api/intervals                                            strategy_chart_bp.strategy_chart_intervals
protected     /strategybuilder/api/multi-strike-oi                                      strategy_chart_bp.multi_strike_oi_data
protected     /strategybuilder/api/strategy-chart                                       strategy_chart_bp.strategy_chart_data
protected     /sudarshan/alerts/api/alerts                                              sudarshan_alerts.create_alert
protected     /sudarshan/alerts/api/alerts/<int:alert_id>                               sudarshan_alerts.change_alert
protected     /sudarshan/alerts/api/alerts/<int:alert_id>                               sudarshan_alerts.delete_alert
protected     /sudarshan/alerts/api/events                                              sudarshan_alerts.events
protected     /sudarshan/alerts/api/events/since                                        sudarshan_alerts.events_since
protected     /sudarshan/alerts/api/groups                                              sudarshan_alerts.create_group
protected     /sudarshan/alerts/api/groups/<int:group_id>                               sudarshan_alerts.change_group
protected     /sudarshan/alerts/api/groups/<int:group_id>                               sudarshan_alerts.delete_group
protected     /sudarshan/alerts/api/groups/sync                                         sudarshan_alerts.sync_groups
protected     /sudarshan/alerts/api/prefs                                               sudarshan_alerts.prefs
protected     /sudarshan/alerts/api/prefs                                               sudarshan_alerts.set_prefs
protected     /sudarshan/alerts/api/state                                               sudarshan_alerts.state
protected     /sudarshan/alerts/api/test                                                sudarshan_alerts.send_test
protected     /sudarshan/autologin/enabled                                              sudarshan_autologin.set_enabled
protected     /sudarshan/autologin/pin                                                  sudarshan_autologin.clear_pin
protected     /sudarshan/autologin/pin                                                  sudarshan_autologin.set_pin
protected     /sudarshan/autologin/status                                               sudarshan_autologin.status
protected     /sudarshan/autologin/test                                                 sudarshan_autologin.test_now
protected     /sudarshan/autologin/totp-secret                                          sudarshan_autologin.clear_totp_secret
protected     /sudarshan/autologin/totp-secret                                          sudarshan_autologin.set_totp_secret
protected     /sudarshan/family/api/audit_list                                          sudarshan_family.audit_list
protected     /sudarshan/family/api/device_list                                         sudarshan_family.device_list
protected     /sudarshan/family/api/device_revoke                                       sudarshan_family.device_revoke
protected     /sudarshan/family/api/invite_create                                       sudarshan_family.invite_create
protected     /sudarshan/family/api/invite_revoke                                       sudarshan_family.invite_revoke
protected     /sudarshan/family/api/member_create                                       sudarshan_family.member_create
protected     /sudarshan/family/api/member_list                                         sudarshan_family.member_list
protected     /sudarshan/family/api/member_remove                                       sudarshan_family.member_remove
protected     /sudarshan/family/api/member_status                                       sudarshan_family.member_status
protected     /sudarshan/family/api/member_update                                       sudarshan_family.member_update
protected     /sudarshan/family/api/share_list                                          sudarshan_family.share_list
protected     /sudarshan/family/api/share_remove                                        sudarshan_family.share_remove
protected     /sudarshan/family/api/share_set                                           sudarshan_family.share_set
protected     /sudarshan/family/api/signin_reset                                        sudarshan_family.signin_reset
protected     /sudarshan/family/api/state                                               sudarshan_family.state
protected     /sudarshan/journal/api/analytics                                          sudarshan_journal.analytics
protected     /sudarshan/journal/api/playbooks                                          sudarshan_journal.playbook_create
protected     /sudarshan/journal/api/playbooks                                          sudarshan_journal.playbooks_list
protected     /sudarshan/journal/api/playbooks/<int:id>                                 sudarshan_journal.playbook_update
protected     /sudarshan/journal/api/reconcile                                          sudarshan_journal.reconcile_now
protected     /sudarshan/journal/api/reports                                            sudarshan_journal.reports_list
protected     /sudarshan/journal/api/reports/<int:id>                                   sudarshan_journal.report_get
protected     /sudarshan/journal/api/reports/<int:id>/file                              sudarshan_journal.report_file
protected     /sudarshan/journal/api/reports/build                                      sudarshan_journal.report_build
protected     /sudarshan/journal/api/settings                                           sudarshan_journal.settings_get
protected     /sudarshan/journal/api/settings                                           sudarshan_journal.settings_set
protected     /sudarshan/journal/api/state                                              sudarshan_journal.state
protected     /sudarshan/journal/api/tags                                               sudarshan_journal.tag_create
protected     /sudarshan/journal/api/tags                                               sudarshan_journal.tags_list
protected     /sudarshan/journal/api/tags/<int:id>                                      sudarshan_journal.tag_update
protected     /sudarshan/journal/api/trips                                              sudarshan_journal.trips
protected     /sudarshan/journal/api/trips/<int:id>                                     sudarshan_journal.trip_get
protected     /sudarshan/journal/api/trips/<int:id>                                     sudarshan_journal.trip_update
protected     /sudarshan/portfolio/api/accounts                                         sudarshan_portfolio.account_create
protected     /sudarshan/portfolio/api/accounts/<int:id>                                sudarshan_portfolio.account_update
protected     /sudarshan/portfolio/api/activities/<int:id>                              sudarshan_portfolio.activity_delete
protected     /sudarshan/portfolio/api/activities/<int:id>                              sudarshan_portfolio.activity_update
protected     /sudarshan/portfolio/api/assets                                           sudarshan_portfolio.asset_create
protected     /sudarshan/portfolio/api/assets/<int:id>                                  sudarshan_portfolio.asset_update
protected     /sudarshan/portfolio/api/assets/<int:id>/prices                           sudarshan_portfolio.price_point_set
protected     /sudarshan/portfolio/api/assets/<int:id>/tab                              sudarshan_portfolio.loan_move
protected     /sudarshan/portfolio/api/imports/<int:id>                                 sudarshan_portfolio.import_discard
protected     /sudarshan/portfolio/api/imports/<int:id>/commit                          sudarshan_portfolio.import_commit
protected     /sudarshan/portfolio/api/members                                          sudarshan_portfolio.member_create
protected     /sudarshan/portfolio/api/members/<int:id>                                 sudarshan_portfolio.member_update
protected     /sudarshan/portfolio/api/mf/search                                        sudarshan_portfolio.mf_search
protected     /sudarshan/portfolio/api/reports/pnl                                      sudarshan_portfolio.report_pnl
protected     /sudarshan/portfolio/api/reports/trades                                   sudarshan_portfolio.report_trades
protected     /sudarshan/portfolio/api/reports/years                                    sudarshan_portfolio.report_years
protected     /sudarshan/portfolio/api/state                                            sudarshan_portfolio.state
protected     /sudarshan/portfolio/api/summary                                          sudarshan_portfolio.summary
protected     /sudarshan/portfolio/api/tabs                                             sudarshan_portfolio.tab_create
protected     /sudarshan/portfolio/api/tabs/<int:id>                                    sudarshan_portfolio.tab_delete
protected     /sudarshan/portfolio/api/tabs/<int:id>                                    sudarshan_portfolio.tab_get
protected     /sudarshan/portfolio/api/tabs/<int:id>                                    sudarshan_portfolio.tab_update
protected     /sudarshan/portfolio/api/tabs/<int:id>/activities                         sudarshan_portfolio.activities_list
protected     /sudarshan/portfolio/api/tabs/<int:id>/activities                         sudarshan_portfolio.activity_create
protected     /sudarshan/portfolio/api/tabs/<int:id>/export.csv                         sudarshan_portfolio.export_csv
protected     /sudarshan/portfolio/api/tabs/<int:id>/imports                            sudarshan_portfolio.import_preview
protected     /sudarshan/portfolio/api/tabs/<int:id>/layout                             sudarshan_portfolio.layout_get
protected     /sudarshan/portfolio/api/tabs/<int:id>/layout                             sudarshan_portfolio.layout_set
protected     /sudarshan/portfolio/api/tabs/<int:id>/performance                        sudarshan_portfolio.performance
protected     /sudarshan/portfolio/api/tabs/<int:id>/views                              sudarshan_portfolio.view_create
protected     /sudarshan/portfolio/api/tabs/<int:id>/views                              sudarshan_portfolio.view_list
protected     /sudarshan/portfolio/api/upstox                                           sudarshan_portfolio.upstox_book
protected     /sudarshan/portfolio/api/upstox/history                                   sudarshan_portfolio.upstox_history
protected     /sudarshan/portfolio/api/views/<int:id>                                   sudarshan_portfolio.view_delete
protected     /sudarshan/portfolio/api/views/<int:id>                                   sudarshan_portfolio.view_update
protected     /sudarshan/risk/audit                                                     sudarshan_risk_bp.audit
protected     /sudarshan/risk/killswitch                                                sudarshan_risk_bp.turn_on
protected     /sudarshan/risk/preview                                                   sudarshan_risk_bp.preview
protected     /sudarshan/risk/resume                                                    sudarshan_risk_bp.resume
protected     /sudarshan/risk/settings                                                  sudarshan_risk_bp.get_settings
protected     /sudarshan/risk/settings                                                  sudarshan_risk_bp.put_settings
protected     /sudarshan/risk/state                                                     sudarshan_risk_bp.state
protected     /sudarshan/risk/status                                                    sudarshan_risk_bp.status
protected     /sudarshan/screener/api/dashboard                                         sudarshan_screener.dashboard
protected     /sudarshan/screener/api/dashboard/order                                   sudarshan_screener.widget_order
protected     /sudarshan/screener/api/dashboard/widgets                                 sudarshan_screener.widget_add
protected     /sudarshan/screener/api/dashboard/widgets/<int:id>                        sudarshan_screener.widget
protected     /sudarshan/screener/api/dashboard/widgets/<int:id>                        sudarshan_screener.widget_remove
protected     /sudarshan/screener/api/dashboard/widgets/<int:id>                        sudarshan_screener.widget_update
protected     /sudarshan/screener/api/membership                                        sudarshan_screener.membership
protected     /sudarshan/screener/api/membership/<int:id>                               sudarshan_screener.membership_update
protected     /sudarshan/screener/api/presets                                           sudarshan_screener.presets
protected     /sudarshan/screener/api/preview                                           sudarshan_screener.preview
protected     /sudarshan/screener/api/runs/<int:id>                                     sudarshan_screener.run
protected     /sudarshan/screener/api/runs/since                                        sudarshan_screener.runs_since
protected     /sudarshan/screener/api/screens                                           sudarshan_screener.create
protected     /sudarshan/screener/api/screens/<int:id>                                  sudarshan_screener.delete
protected     /sudarshan/screener/api/screens/<int:id>                                  sudarshan_screener.update
protected     /sudarshan/screener/api/screens/<int:id>/results                          sudarshan_screener.results
protected     /sudarshan/screener/api/screens/<int:id>/runs                             sudarshan_screener.runs
protected     /sudarshan/screener/api/state                                             sudarshan_screener.state
protected     /sudarshan/screener/api/today                                             sudarshan_screener.today
protected     /sudarshan/screener/api/universes                                         sudarshan_screener.universes
protected     /sudarshan/session/instrument                                             sudarshan_session_bp.instrument
protected     /sudarshan/watchlists/api/copy                                            sudarshan_watchlists.copy
protected     /sudarshan/watchlists/api/favourites                                      sudarshan_watchlists.favourites
protected     /sudarshan/watchlists/api/flags                                           sudarshan_watchlists.flags
protected     /sudarshan/watchlists/api/lists                                           sudarshan_watchlists.lists
protected     /sudarshan/watchlists/api/lists/<int:id>/arrange                          sudarshan_watchlists.arrange
protected     /sudarshan/watchlists/api/prefs/<ref>                                     sudarshan_watchlists.pref
protected     /sudarshan/watchlists/api/ranked                                          sudarshan_watchlists.ranked
protected     /sudarshan/watchlists/api/resolve                                         sudarshan_watchlists.resolve
protected     /sudarshan/watchlists/api/rows                                            sudarshan_watchlists.row_add
protected     /sudarshan/watchlists/api/rows/<int:id>                                   sudarshan_watchlists.row_edit
protected     /sudarshan/watchlists/api/rows/<int:id>                                   sudarshan_watchlists.row_remove
protected     /sudarshan/watchlists/api/search                                          sudarshan_watchlists.search
protected     /sudarshan/watchlists/api/sections                                        sudarshan_watchlists.section_add
protected     /sudarshan/watchlists/api/sections/<int:id>                               sudarshan_watchlists.section_edit
protected     /sudarshan/watchlists/api/sections/<int:id>                               sudarshan_watchlists.section_remove
protected     /sudarshan/watchlists/api/state                                           sudarshan_watchlists.state
protected     /sudarshan/watchlists/api/strikes                                         sudarshan_watchlists.strikes
protected     /sudarshan/watchlists/api/symbol                                          sudarshan_watchlists.symbol
protected     /sudarshan/watchlists/api/symbols/<k>                                     sudarshan_watchlists.mark
protected     /sudarshan/watchlists/api/symbols/expired/clear                           sudarshan_watchlists.expired_clear
protected     /sudarshan/watchlists/api/tv/export                                       sudarshan_watchlists.tv_export
protected     /sudarshan/watchlists/api/tv/import                                       sudarshan_watchlists.tv_import
protected     /sudarshan/watchlists/api/tv/preview                                      sudarshan_watchlists.tv_preview
protected     /sudarshan/watchlists/api/view                                            sudarshan_watchlists.view
protected     /telegram/api/analytics                                                   telegram_bp.api_analytics
protected     /telegram/api/config                                                      telegram_bp.api_config
protected     /telegram/api/index                                                       telegram_bp.api_index
protected     /telegram/api/users                                                       telegram_bp.api_users
protected     /telegram/bot/start                                                       telegram_bp.start_bot
protected     /telegram/bot/status                                                      telegram_bp.bot_status
protected     /telegram/bot/stop                                                        telegram_bp.stop_bot
protected     /telegram/broadcast                                                       telegram_bp.broadcast
protected     /telegram/config                                                          telegram_bp.configuration
protected     /telegram/send-message                                                    telegram_bp.send_message
protected     /telegram/test-message                                                    telegram_bp.send_test_message
protected     /telegram/user/<int:telegram_id>/unlink                                   telegram_bp.unlink_user
protected     /test_chart                                                               pnltracker_bp.test_chart
protected     /tradebook                                                                orders_bp.tradebook
protected     /tradebook/export                                                         orders_bp.export_tradebook
protected     /tradingview/                                                             tv_json_bp.tradingview_json
protected     /traffic/                                                                 traffic_bp.traffic_dashboard
protected     /traffic/api/logs                                                         traffic_bp.get_logs
protected     /traffic/api/stats                                                        traffic_bp.get_stats
protected     /traffic/export                                                           traffic_bp.export_logs
protected     /volsurface/api/surface-data                                              vol_surface_bp.surface_data
protected     /watchlist/api/lists                                                      watchlist_bp.create_list
protected     /watchlist/api/lists                                                      watchlist_bp.lists
protected     /watchlist/api/lists/<int:watchlist_id>                                   watchlist_bp.remove_list
protected     /watchlist/api/lists/<int:watchlist_id>                                   watchlist_bp.rename_list
protected     /watchlist/api/lists/<int:watchlist_id>/clear                             watchlist_bp.clear_list
protected     /watchlist/api/lists/<int:watchlist_id>/items                             watchlist_bp.add_symbol
protected     /watchlist/api/lists/<int:watchlist_id>/items/<int:item_id>               watchlist_bp.remove_symbol
protected     /watchlist/api/lists/<int:watchlist_id>/items/order                       watchlist_bp.order_symbols
protected     /websocket/dashboard                                                      websocket.websocket_dashboard
protected     /websocket/test                                                           websocket.websocket_test
protected     /whatsapp/bot/start                                                       whatsapp_bp.start_bot
protected     /whatsapp/bot/status                                                      whatsapp_bp.bot_status
protected     /whatsapp/bot/stop                                                        whatsapp_bp.stop_bot
protected     /whatsapp/broadcast                                                       whatsapp_bp.broadcast
protected     /whatsapp/config                                                          whatsapp_bp.get_config
protected     /whatsapp/config                                                          whatsapp_bp.update_config
protected     /whatsapp/pair                                                            whatsapp_bp.start_pair
protected     /whatsapp/pair/status                                                     whatsapp_bp.pair_status
protected     /whatsapp/send                                                            whatsapp_bp.send_to_phone
protected     /whatsapp/stats                                                           whatsapp_bp.stats
protected     /whatsapp/test-message                                                    whatsapp_bp.test_message
protected     /whatsapp/unlink                                                          whatsapp_bp.unlink_device
protected     /whatsapp/user/<path:whatsapp_jid>/unlink                                 whatsapp_bp.unlink_user
protected     /whatsapp/users                                                           whatsapp_bp.list_users
"""


class Finding(NamedTuple):
    """How a route refuses every caller today, though not cleanly."""

    status: int
    #: A piece of that answer: of its body, its redirect or the exception its view raised.
    says: str
    why: str


#: Routes that refuse every caller, but not cleanly, as the walk saw them on 14 Nov 2026 (P6 T1).
#: Each runs as an expected failure; once its route answers cleanly the suite fails until its line
#: goes, and a route that starts to answer anything else fails at once. The family view is switched
#: on only after the G5 suite is green (P6 1), and that includes this list being empty.
FINDINGS: dict[tuple[str, str, str], Finding] = {
    ("/test_chart", "pnltracker_bp.test_chart", "GET"): Finding(
        302,
        "TemplateNotFound",
        "no session check at all: it refuses only because its template is gone and the error "
        "page's redirect answers (blueprints/pnltracker.py)",
    ),
}

#: ``/sudarshan/*`` rules the forged session of a user who is not the owner is not held to: they
#: are not owner bridges, and read the session with upstream's own check, as upstream's routes do
#: (cross-design Y15: such a session needs the app's key, which no viewer has).
NOT_OWNER_ONLY = {
    "/sudarshan/session/instrument": (
        "an instrument's trading hours for the chart: upstream's session check, no account data"
    ),
}


def _parse(text: str) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """The table as ``{(rule, endpoint): class}`` and ``{(rule, endpoint): reason}``."""
    routes: dict[tuple[str, str], str] = {}
    reasons: dict[tuple[str, str], str] = {}
    group = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            group = ""
            continue
        if line.startswith("##"):
            group = line[2:].strip()
            continue
        if line.startswith("#"):
            continue
        left, _, reason = line.partition("#")
        fields = left.split()
        if len(fields) != 3 or fields[0] not in CLASSES:
            raise ValueError(f"TABLE line {number} is not '<class> <rule> <endpoint>': {raw!r}")
        cls, rule, endpoint = fields
        if (rule, endpoint) in routes:
            raise ValueError(f"TABLE line {number} names {rule} ({endpoint}) a second time")
        reason = reason.strip() or group
        if cls in NEEDS_REASON and not reason:
            raise ValueError(f"TABLE line {number} is {cls}, so it says why: {raw!r}")
        routes[(rule, endpoint)] = cls
        reasons[(rule, endpoint)] = reason
    return routes, reasons


ROUTES, REASONS = _parse(TABLE)


def line_for(rule: str, endpoint: str, cls: str = PROTECTED) -> str:
    """A rule's table line, to paste in."""
    return f"{cls:<14}{rule}  {endpoint}"
