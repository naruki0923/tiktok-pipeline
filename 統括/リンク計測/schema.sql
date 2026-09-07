CREATE TABLE IF NOT EXISTS clicks (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       INTEGER NOT NULL,   -- クリック時刻（epoch ミリ秒 / UTC）
  src      TEXT,               -- 流入元タグ（?src=015 など。既定 "profile"）
  referer  TEXT                -- 参照元（取れれば）
);
CREATE INDEX IF NOT EXISTS idx_clicks_ts  ON clicks(ts);
CREATE INDEX IF NOT EXISTS idx_clicks_src ON clicks(src);
