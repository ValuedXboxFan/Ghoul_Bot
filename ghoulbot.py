import pymysql.cursors
from settings import *
from media_get import media

import random
import asyncio
import aiohttp
import json
from discord import Game, utils, Embed
from discord.ext.commands import Bot


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
            print(f'* Connected to Discord as {self.bot.user.name}')


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
                await context.send('Failure! You can only use this command in the Test-Channel channel')


        # Channel gated ping command for general channel
        @self.bot.command(name='general-ping')
        async def general_ping(context):
            if context.message.channel.id == ch_general:
                await context.send('Success! You\'re in the right place.' )
            else:
                await context.send('Failure! You can only use this command in the General channel')



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

        @self.bot.command(name='movie')
        async def movie(context, *args):
            if args[-1][:2] == '--':
                try:
                    movie = media(media_type='movie', title=' '.join(args[:-1]), year=args[-1][2:])
                except:
                    await context.send('No results')
                    return
            else:
                try:
                    movie = media(media_type='movie', title=' '.join(args))
                except:
                    await context.send('No results')
                    return
            await context.send(embed=self.build_media_embed(movie))


        @self.bot.command(name='tvshow')
        async def tv_show(context, *args):
            if args[-1][:2] == '--':
                try:
                    tv_show = media(media_type='tv_show', title=' '.join(args[:-1]), year=args[-1][2:])
                except:
                    await context.send('No results')
                    return
            else:
                try:
                    tv_show = media(media_type='tv_show', title=' '.join(args))
                except:
                    await context.send('No results')
                    return
            await context.send(embed=self.build_media_embed(tv_show))


        @self.bot.command(name='videogame')
        async def video_game(context, *args):
            video_game = media(media_type='video_game', title=' '.join(args))
            await context.send(embed=self.build_media_embed(video_game))


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
                sql = "INSERT INTO `gt_users` (`member_id`, `server_join_date`, `points`) VALUES ({0}, {1}, {2})"
                cursor.execute(sql.format(member.id, member.joined_at, 0))
            self.db.commit()
            print(f'Added {member.id} to database')
        except Exception as e:
            print (f'Error adding member: {e}')


    # Get user by member ID
    def get_user(self, member_id):
        try:
            with self.db.cursor() as cursor:
                # Read a single record
                sql = "SELECT `member_id`,`server_join_date`,`points` FROM `gt_users` WHERE `member_id`={}"
                cursor.execute(sql.format(member_id))
                result = cursor.fetchone()
                if not result:
                    print (f'User does not exist: {member_id}')
                else:
                    return result
        except Exception as e:
            print(f'- Error looking up userid {member_id}.\n{e}')


    # Update user points
    def update_points(self, member, points):
        member_info = self.get_user(member.id)
        with self.db.cursor() as cursor:
            try:
                point_total = member_info["points"] + points
                sql = "UPDATE gt_users SET points={0} WHERE member_id={1}"
                cursor.execute(sql.format(point_total, member.id))
                self.db.commit()
                print(f'* Updated user {member.name} points from {member_info["points"]} to {point_total}.')
            except Exception as e:
                print(f'- Error updating points for {member.name}: .\n{e}')


    # Build media Embed
    def build_media_embed(self, media_item):
        if media_item.media_type == 'movie':
            embed_color = 0x00ff00

        if media_item.media_type == 'video_game':
            embed_color = 0x0000ff

        if media_item.media_type == 'tv_show':
            embed_color = 0xff0000

        embed = Embed(color=embed_color)
        embed.title = media_item.title
        embed.set_footer(text=media_item.source_legal)
        embed.set_author(name=media_item.source, icon_url=media_item.source_logo)
        if media_item.overview:
            embed.add_field(name='Overview:', value=media_item.overview, inline=False)
        if media_item.release_date:
            embed.add_field(name='Release Date:', value=media_item.release_date, inline=True)
        if media_item.img:
            embed.set_image(url=media_item.img)
            print(media_item.img)
        return embed


    def run(self):
        self.bot.run(self.token)

if __name__ == '__main__':
    bot = GhoulBot(BOT_TOKEN)
    bot.run()
