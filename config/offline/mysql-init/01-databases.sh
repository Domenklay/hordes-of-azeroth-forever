#!/bin/bash
# Adapted from Domenklay/WoWForEver docker/mysql-init/01-trinity.sh,
# revision a7e7747778042b537d062f019bbf2465d67a2863 (GPL-2.0).
# Runs only when MySQL initializes an empty saves volume.
set -euo pipefail
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot <<SQL
CREATE DATABASE IF NOT EXISTS auth DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS characters DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS world DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS hotfixes DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'trinity'@'%' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON auth.* TO 'trinity'@'%';
GRANT ALL PRIVILEGES ON characters.* TO 'trinity'@'%';
GRANT ALL PRIVILEGES ON world.* TO 'trinity'@'%';
GRANT ALL PRIVILEGES ON hotfixes.* TO 'trinity'@'%';
FLUSH PRIVILEGES;
SQL
