from broker.upstox.mapping.order_data import transform_holdings_data


def test_transform_holdings_uses_standard_ltp_contract():
    out = transform_holdings_data(
        [
            {
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "quantity": 2,
                "product": "D",
                "average_price": 100,
                "last_price": 120,
                "pnl": 40,
            }
        ]
    )

    assert out == [
        {
            "symbol": "INFY",
            "exchange": "NSE",
            "quantity": 2,
            "product": "D",
            "average_price": 100.0,
            "ltp": 120.0,
            "pnl": 40.0,
            "pnlpercent": 20.0,
        }
    ]


def test_transform_holdings_handles_zero_average_price():
    out = transform_holdings_data(
        [
            {
                "tradingsymbol": "BONUS",
                "exchange": "NSE",
                "quantity": 1,
                "product": "D",
                "average_price": None,
                "last_price": 50,
                "pnl": 5,
            }
        ]
    )

    assert out[0]["average_price"] == 0.0
    assert out[0]["ltp"] == 50.0
    assert out[0]["pnlpercent"] == 0.0


def test_transform_holdings_passes_the_isin_only_when_there_is_one():
    out = transform_holdings_data(
        [
            {"tradingsymbol": "A", "exchange": "BSE", "quantity": 1, "isin": "INE002A01018"},
            {"tradingsymbol": "B", "exchange": "NSE", "quantity": 1},
        ]
    )

    assert out[0]["isin"] == "INE002A01018"
    assert "isin" not in out[1]


def _raw_holding(**fields) -> dict:
    """One row of Upstox's GET /v2/portfolio/long-term-holdings answer, with every field Upstox
    documents for it. The values are made up: no real holding, company or ISIN."""
    row = {
        "isin": "INE000X01015",
        "cnc_used_quantity": 0,
        "collateral_type": "WC",
        "company_name": "SYNTHETIC CO LTD",
        "haircut": 0.2,
        "product": "D",
        "quantity": 10,
        "trading_symbol": "SYNTHCO",
        "tradingsymbol": "SYNTHCO",
        "last_price": 112.5,
        "close_price": 110.0,
        "pnl": 125.0,
        "day_change": 2.5,
        "day_change_percentage": 2.27,
        "instrument_token": "NSE_EQ|INE000X01015",
        "average_price": 100.0,
        "collateral_quantity": 0,
        "collateral_update_quantity": 0,
        "t1_quantity": 0,
        "exchange": "NSE",
    }
    return row | fields


def test_golden_a_raw_upstox_holdings_answer_through_the_holdings_chain():
    """SUDARSHAN v0.9.0 review L6: a field added to a broker book gets golden vectors built from
    the broker's raw rows. The same chain as POST /api/v1/holdings (services.holdings_service):
    map_portfolio_data -> calculate_portfolio_statistics -> transform_holdings_data ->
    format_holdings_data. Every field of the answer is written out, so one that appears,
    disappears or changes fails here first; ``isin`` reaches each row, on NSE and on BSE alike,
    and nothing else Upstox sends does."""
    from broker.upstox.mapping.order_data import (
        calculate_portfolio_statistics,
        map_portfolio_data,
    )
    from services.holdings_service import format_holdings_data, format_statistics

    raw = {
        "status": "success",
        "data": [
            _raw_holding(),
            _raw_holding(
                exchange="BSE",
                instrument_token="BSE_EQ|INE000X01015",
                quantity=5,
                last_price=112.4,
                pnl=62.0,
            ),
        ],
    }
    holdings = map_portfolio_data(raw)
    stats = calculate_portfolio_statistics(holdings)
    rows = format_holdings_data(transform_holdings_data(holdings))

    assert rows == [
        {
            "symbol": "SYNTHCO",
            "exchange": "NSE",
            "quantity": 10,
            "product": "CNC",
            "average_price": 100.0,
            "ltp": 112.5,
            "pnl": 125.0,
            "pnlpercent": 12.5,
            "isin": "INE000X01015",
        },
        {
            "symbol": "SYNTHCO",
            "exchange": "BSE",
            "quantity": 5,
            "product": "CNC",
            "average_price": 100.0,
            "ltp": 112.4,
            "pnl": 62.0,
            "pnlpercent": 12.4,
            "isin": "INE000X01015",
        },
    ]
    assert format_statistics(stats) == {
        "totalholdingvalue": 1687.0,
        "totalinvvalue": 1500.0,
        "totalprofitandloss": 187.0,
        "totalpnlpercentage": 12.47,
    }
