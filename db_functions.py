import pymysql.cursors
from settings import *


# Connet to db
def connect_db():
    connection = pymysql.connect(host=DB_HOST,
                                 user=DB_USER,
                                 password=DB_PASS,
                                 db=DB_NAME,
                                 charset='utf8mb4',
                                 cursorclass=pymysql.cursors.DictCursor)

    print(f'* Connected to {DB_NAME}')
    return connection


# Ensure existing members are stored in db
def add_all_members_to_db(client):
    for member in client.bot.get_all_members():
        add_member_to_db(client, member)


# Add member to db
def add_member_to_db(client, member):
    if get_user(client, member.id):
        return
    try:
        with client.db.cursor() as cursor:
            sql = "INSERT INTO `gt_users` (`member_id`, `server_join_date`, `points`) VALUES (%s, %s, %s)"
            cursor.execute(sql, (member.id, member.joined_at, 0))
        client.db.commit()
        print(f'Added {member.id} to database')
    except Exception as e:
        print (f'Error adding member: {e}')


# Get user by member ID
def get_user(client, member_id):
    try:
        with client.db.cursor() as cursor:
            # Read a single record
            sql = "SELECT `member_id`,`server_join_date`,`points` FROM `gt_users` WHERE `member_id`=%s"
            cursor.execute(sql, (member_id))
            result = cursor.fetchone()
            if not result:
                print (f'User does not exist: {member_id}')
            else:
                return result
    except Exception as e:
        print(f'- Error looking up userid {member_id}.\n{e}')


# Update user points
def update_points(client, member, points):
    member_info = get_user(client, member.id)
    with client.db.cursor() as cursor:
        try:
            point_total = member_info["points"] + points
            sql = "UPDATE gt_users SET points=%s WHERE member_id=%s"
            cursor.execute(sql, (point_total, member.id))
            client.db.commit()
            print(f'* Updated user {member.name} points from {member_info["points"]} to {point_total}.')
        except Exception as e:
            print(f'- Error updating points for {member.name}: .\n{e}')
