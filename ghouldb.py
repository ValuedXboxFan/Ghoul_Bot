import pymysql.cursors


class db_connection():

    def __init__(self, *, host, user, password, db,):
        db_connection.connection = pymysql.connect(host=host,
                                     user=user,
                                     password=password,
                                     db=db,
                                     charset='utf8mb4',
                                     cursorclass=pymysql.cursors.DictCursor)
        print(f'* Connected to {db}')


    # Ensure existing members are stored in db
    def add_all_members_to_db(self, members):
        for member in members:
            self.add_member_to_db(member)


    # Add member to db
    def add_member_to_db(self, member):
        if self.get_user(member.id):
            return
        try:
            with self.connection.cursor() as cursor:
                sql = "INSERT INTO `users` (`member_id`, `server_join_date`, `a_points_total`) VALUES (%s, %s, %s)"
                cursor.execute(sql, (member.id, member.joined_at, 0))
            self.connection.commit()
            print(f'Added {member.id} to database')
        except Exception as e:
            print (f'Error adding member: {e}')


    # Get user by member ID
    def get_user(self, member_id):
        try:
            with self.connection.cursor() as cursor:
                # Read a single record
                sql = "SELECT `member_id`,`server_join_date`,`a_points_total` FROM `users` WHERE `member_id`=%s"
                cursor.execute(sql, (member_id))
                result = cursor.fetchone()
                if not result:
                    print (f'User does not exist: {member_id}')
                else:
                    return result
        except Exception as e:
            print(f'- Error looking up userid {member_id}.\n{e}')


    # Update user astral point total
    def update_points(self, member, points):
        member_info = self.get_user(member.id)
        with self.connection.cursor() as cursor:
            try:
                point_total = member_info["a_points_total"] + points
                sql = "UPDATE users SET a_points_total=%s WHERE member_id=%s"
                cursor.execute(sql, (point_total, member.id))
                self.connection.commit()
                print(f'* Updated user {member.name} points from {member_info["a_points_total"]} to {point_total}.')
            except Exception as e:
                print(f'- Error updating points for {member.name}: .\n{e}')
