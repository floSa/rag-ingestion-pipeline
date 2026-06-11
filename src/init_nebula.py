import time

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

config = Config()
pool = ConnectionPool()
if not pool.init([('graphd', 9669)], config):
    print("Failed to connect to graphd")
    exit(1)

session = pool.get_session('root', 'nebula')

print("Adding hosts...")
res = session.execute('ADD HOSTS "storaged":9779;')
print("ADD HOSTS:", res.is_succeeded(), res.error_msg())

time.sleep(5)

print("Showing hosts...")
res = session.execute('SHOW HOSTS;')
if res.is_succeeded():
    for row in res.rows():
        print(row)
else:
    print("SHOW HOSTS FAILED:", res.error_msg())

print("Creating space...")
res = session.execute(
    "CREATE SPACE IF NOT EXISTS rag_space "
    "(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(64));"
)
print("CREATE SPACE:", res.is_succeeded(), res.error_msg())

time.sleep(10)

print("Showing spaces...")
res = session.execute('SHOW SPACES;')
if res.is_succeeded():
    for row in res.rows():
        print(row)
else:
    print("SHOW SPACES FAILED:", res.error_msg())

pool.close()
