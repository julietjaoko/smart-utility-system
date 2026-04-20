import pymysql

# 1. Tell PyMySQL to pretend to be MySQLdb
pymysql.install_as_MySQLdb()

# 2. Trick Django into accepting the version
import MySQLdb
MySQLdb.version_info = (2, 2, 1, 'final', 0)