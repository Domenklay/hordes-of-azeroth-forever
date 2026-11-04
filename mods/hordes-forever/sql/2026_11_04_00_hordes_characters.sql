-- Additive mode tables; ordinary character/account data is not altered.
CREATE TABLE IF NOT EXISTS `hoa_forever_progress` (
  `guid` bigint unsigned NOT NULL,
  `runs` int unsigned NOT NULL DEFAULT 0,
  `wins` int unsigned NOT NULL DEFAULT 0,
  `kills` bigint unsigned NOT NULL DEFAULT 0,
  `best_wave` int unsigned NOT NULL DEFAULT 0,
  `coins` bigint unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`guid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `hoa_forever_return` (
  `guid` bigint unsigned NOT NULL,
  `map` int unsigned NOT NULL,
  `x` float NOT NULL, `y` float NOT NULL, `z` float NOT NULL, `o` float NOT NULL,
  PRIMARY KEY (`guid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
