import pymysql.cursors
from settings import *

import random
import asyncio
import aiohttp
import json
from discord import Game, utils
#from discord import utils
from discord.ext.commands import Bot


# Connet to db
def connect_db():
    connection = pymysql.connect(host=DB_HOST,
                                 user=DB_USER,
                                 password=DB_PASS,
                                 db=DB_NAME,
                                 charset='utf8mb4',
                                 cursorclass=pymysql.cursors.DictCursor)

    print('* Connected to %s' % DB_NAME)
    return connection


class GhoulBot():

    def __init__(self, token):
        self.db = connect_db()
        self.bot = Bot(command_prefix=BOT_PREFIX)
        self.token = token
        self.prepare_client()

    def prepare_client(self):


        @self.bot.event
        async def on_ready():
            await self.bot.change_presence()
            self.add_all_members_to_db()
            print("* Connected to Discord as " + self.bot.user.name)


        # Watch for new server members and add them to db
        @self.bot.event
        async def on_member_join(member):
            self.add_member_to_db(member)


        # Channel gated ping command for test-channel channel
        @self.bot.command(name='testchannel-ping')
        async def testchannel_ping(context):
            if context.message.channel.id == ch_test_channel:
                await context.send('Success! You\'re in the right place.' )
            else:
                await context.send('Failure! You can only use this command in %s' % self.bot.get_channel(ch_test_channel))


        # Channel gated ping command for general channel
        @self.bot.command(name='general-ping')
        async def general_ping(context):
            if context.message.channel.id == ch_general:
                await context.send('Success! You\'re in the right place.' )
            else:
                await context.send('Failure! You can only use this command in the %s channel' % self.bot.get_channel(ch_general))



        # Add/remove points from user
        @self.bot.command(name='gimmepoints')
        async def gimmepoints(context, arg):
            try:
                point_change = int(arg)
            except Exception as e:
                await context.send('Please pass a number as an argument')
                pass
            self.update_points(context.message.author, point_change)


        # Phil's message dumbwaiter
        @self.bot.command(name='msgch')
        async def message_channel(context, channel_name, *, msg):
            channel = utils.get(self.bot.get_all_channels(), name=channel_name)
            if channel:
                await channel.send(msg)
            else:
                await context.send('That channel doesn\'t exist.')



    # Ensure existing members are stored in db
    def add_all_members_to_db(self):
        for member in self.bot.get_all_members():
            self.add_member_to_db(member)


    # Add member to db
    def add_member_to_db(self, member):
        if self.get_user(member.id):
            return

        try:
            with self.db.cursor() as cursor:
                sql = "INSERT INTO `gt_users` (`member_id`, `server_join_date`, `points`) VALUES (%s, %s, %s)"
                cursor.execute(sql, (member.id, member.joined_at, 0))
            self.db.commit()
            print("Added %s to database" % member.id)
        except Exception as e:
            print ("Error adding member: %s" % e)


    # Get user by member ID
    def get_user(self, member_id):
        try:
            with self.db.cursor() as cursor:
                # Read a single record
                sql = "SELECT `member_id`,`server_join_date`,`points` FROM `gt_users` WHERE `member_id`=%s"
                cursor.execute(sql, (member_id,))
                result = cursor.fetchone()
                if not result:
                    print ("User does not exist: %s" % member_id)
                else:
                    return result
        except Exception as e:
            print('Error looking up userid %s.\n%s' % (member_id, e))


    # Update user points
    def update_points(self, member, points):
        member_info = self.get_user(member.id)
        with self.db.cursor() as cursor:
            try:
                sql = "UPDATE gt_users SET points=%s WHERE member_id=%s"
                point_total = member_info['points'] + points
                cursor.execute(sql, (point_total, member.id))
                self.db.commit()
                print("* Updated user %s points from %s to %s." %
                      (member.name, member_info['points'], point_total))
            except Exception as e:
                print("- Error updating points for %s: .\n%s" % (member.name, e))


    def run(self):
        self.bot.run(self.token)

if __name__ == '__main__':
    bot = GhoulBot(BOT_TOKEN)
    bot.run()
