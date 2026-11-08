-- A portfolio.db written by the v0.5.0 code (commit 2dc208a): the Upstox tab, a pinned tab 'Home',
-- two buys, two loans entered with that release's Loan entry (no tab), user_version 1.
-- Made by running 2dc208a's Book against an empty file, then sqlite3 iterdump. Not edited by hand.
BEGIN TRANSACTION;
CREATE TABLE account (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        member_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        broker TEXT,
        note TEXT,
        system INTEGER NOT NULL DEFAULT 0,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
INSERT INTO "account" VALUES(1,'owner',1,'Upstox','upstox','Upstox',NULL,1,NULL,1793000000000,1793000000000,1);
INSERT INTO "account" VALUES(2,'owner',1,'Zerodha','demat',NULL,NULL,0,'a-1',1793000000000,1793000000000,1);
CREATE TABLE activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        tab_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        trade_date INTEGER NOT NULL,
        account_id INTEGER,
        to_account_id INTEGER,
        asset_id INTEGER,
        qty_u INTEGER,
        price_u INTEGER,
        amount_paise INTEGER,
        charges_paise INTEGER NOT NULL DEFAULT 0,
        charges_detail TEXT,
        tds_paise INTEGER NOT NULL DEFAULT 0,
        value_paise INTEGER,
        ratio_num INTEGER,
        ratio_den INTEGER,
        lot_date INTEGER,
        status TEXT NOT NULL DEFAULT 'posted',
        note TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        batch_id INTEGER,
        import_key TEXT,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
INSERT INTO "activity" VALUES(1,'owner',2,1,'BUY',20261001,2,NULL,2,2000000,NULL,1000000,0,NULL,0,NULL,NULL,NULL,NULL,'posted',NULL,'manual',NULL,NULL,'e-1',1793000000000,1793000000000);
INSERT INTO "activity" VALUES(2,'owner',2,2,'BUY',20261002,2,NULL,1,5000000,NULL,500000,0,NULL,0,NULL,NULL,NULL,NULL,'posted',NULL,'manual',NULL,NULL,'e-2',1793000000000,1793000000000);
CREATE TABLE asset (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        exchange TEXT,
        symbol TEXT,
        isin TEXT,
        instrument_key TEXT,
        tick_size_u INTEGER,
        amfi_code TEXT,
        unit_label TEXT,
        asset_class TEXT NOT NULL,
        sector TEXT,
        mcap TEXT,
        tags TEXT NOT NULL DEFAULT '[]',
        resolved_at INTEGER,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
INSERT INTO "asset" VALUES(1,'owner','listed','Alpha','NSE','ALPHA','INE000A01011',NULL,NULL,NULL,NULL,'equity',NULL,NULL,'[]',NULL,'s-1',1793000000000,1793000000000,1);
INSERT INTO "asset" VALUES(2,'owner','manual','Gold coin',NULL,NULL,NULL,NULL,NULL,NULL,'g','gold',NULL,NULL,'[]',NULL,'g-1',1793000000000,1793000000000,1);
INSERT INTO "asset" VALUES(3,'owner','liability','Home loan',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'other',NULL,NULL,'[]',NULL,'l-1',1793000000000,1793000000000,1);
INSERT INTO "asset" VALUES(4,'owner','liability','Car loan',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'other',NULL,NULL,'[]',NULL,'l-2',1793000000000,1793000000000,1);
CREATE TABLE asset_alias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
INSERT INTO "asset_alias" VALUES(1,'owner',1,'symbol','ALPHA',1793000000000,1793000000000);
INSERT INTO "asset_alias" VALUES(2,'owner',1,'isin','INE000A01011',1793000000000,1793000000000);
CREATE TABLE benchmark_close (
        index_key TEXT NOT NULL,
        day INTEGER NOT NULL,
        close_u INTEGER NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY (index_key, day)
    );
CREATE TABLE import_batch (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        tab_id INTEGER NOT NULL,
        format TEXT NOT NULL,
        rows_total INTEGER NOT NULL DEFAULT 0,
        rows_new INTEGER NOT NULL DEFAULT 0,
        rows_dup INTEGER NOT NULL DEFAULT 0,
        rows_bad INTEGER NOT NULL DEFAULT 0,
        preview TEXT,
        status TEXT NOT NULL,
        tab_version INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
CREATE TABLE job_run (
        name TEXT NOT NULL,
        day INTEGER NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        at INTEGER NOT NULL,
        PRIMARY KEY (name, day)
    );
CREATE TABLE member (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        relation TEXT,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
INSERT INTO "member" VALUES(1,'owner','Me',NULL,NULL,1793000000000,1793000000000,1);
CREATE TABLE mf_scheme (
        amfi_code TEXT PRIMARY KEY,
        isin_growth TEXT,
        isin_reinvest TEXT,
        name TEXT NOT NULL,
        category TEXT,
        amc TEXT,
        nav_u INTEGER,
        nav_date INTEGER,
        prev_nav_u INTEGER,
        prev_nav_date INTEGER,
        fetched_at INTEGER NOT NULL
    );
CREATE TABLE price_eod (
        asset_id INTEGER NOT NULL,
        day INTEGER NOT NULL,
        principal TEXT NOT NULL,
        price_u INTEGER NOT NULL,
        prev_close_u INTEGER,
        basis TEXT NOT NULL,
        source TEXT NOT NULL,
        late INTEGER NOT NULL DEFAULT 0,
        at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (asset_id, day)
    );
CREATE TABLE price_point (
        asset_id INTEGER NOT NULL,
        day INTEGER NOT NULL,
        principal TEXT NOT NULL,
        price_u INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (asset_id, day)
    );
INSERT INTO "price_point" VALUES(2,20261001,'owner',60000000,1793000000000,1793000000000);
INSERT INTO "price_point" VALUES(3,20261101,'owner',50000000000,1793000000000,1793000000000);
INSERT INTO "price_point" VALUES(4,20261102,'owner',4000000000,1793000000000,1793000000000);
CREATE TABLE saved_view (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        tab_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        spec TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
CREATE TABLE tab (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        name_alt TEXT,
        kind TEXT NOT NULL,
        icon TEXT NOT NULL,
        colour TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        system INTEGER NOT NULL DEFAULT 0,
        manual_old_days INTEGER NOT NULL DEFAULT 30,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
INSERT INTO "tab" VALUES(1,'owner','Upstox',NULL,'upstox','landmark','brand',0,1,0,1,30,NULL,1793000000000,1793000000000,1);
INSERT INTO "tab" VALUES(2,'owner','Home',NULL,'mixed','briefcase','brand',1,1,0,0,30,'t-1',1793000000000,1793000000000,3);
CREATE TABLE tab_layout (
        tab_id INTEGER PRIMARY KEY,
        principal TEXT NOT NULL,
        spec TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
CREATE TABLE upstox_eod (
        day INTEGER NOT NULL,
        principal TEXT NOT NULL,
        read_at INTEGER NOT NULL,
        holdings_value_paise INTEGER,
        holdings_cost_paise INTEGER,
        positions_pnl_paise INTEGER,
        funds TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, day)
    );
CREATE TABLE upstox_eod_holding (
        day INTEGER NOT NULL,
        instrument_key TEXT NOT NULL,
        principal TEXT NOT NULL,
        asset_id INTEGER,
        qty INTEGER NOT NULL,
        t1_qty INTEGER NOT NULL DEFAULT 0,
        avg_price_u INTEGER,
        price_u INTEGER,
        basis TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, day, instrument_key)
    );
CREATE INDEX member_principal ON member (principal, id);
CREATE UNIQUE INDEX member_request
        ON member (principal, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX account_principal ON account (principal, member_id);
CREATE UNIQUE INDEX account_request
        ON account (principal, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX tab_order ON tab (principal, archived, position);
CREATE UNIQUE INDEX tab_request
        ON tab (principal, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX saved_view_tab ON saved_view (principal, tab_id);
CREATE INDEX asset_principal ON asset (principal, kind);
CREATE UNIQUE INDEX asset_isin
        ON asset (principal, isin) WHERE isin IS NOT NULL;
CREATE UNIQUE INDEX asset_amfi
        ON asset (principal, amfi_code) WHERE amfi_code IS NOT NULL;
CREATE UNIQUE INDEX asset_request
        ON asset (principal, request_id) WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX asset_alias_value ON asset_alias (principal, kind, value);
CREATE INDEX asset_alias_asset ON asset_alias (asset_id);
CREATE INDEX activity_order ON activity (principal, tab_id, trade_date, seq);
CREATE INDEX activity_asset ON activity (principal, asset_id);
CREATE UNIQUE INDEX activity_import
        ON activity (tab_id, import_key) WHERE import_key IS NOT NULL;
CREATE UNIQUE INDEX activity_request
        ON activity (principal, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX import_batch_tab ON import_batch (principal, tab_id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('member',1);
INSERT INTO "sqlite_sequence" VALUES('account',2);
INSERT INTO "sqlite_sequence" VALUES('tab',2);
INSERT INTO "sqlite_sequence" VALUES('asset',4);
INSERT INTO "sqlite_sequence" VALUES('asset_alias',2);
INSERT INTO "sqlite_sequence" VALUES('activity',2);
COMMIT;
