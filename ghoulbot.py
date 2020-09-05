import pymysql.cursors
from settings import *
from media_get import media

import os
import asyncio
import aiohttp
import json
from custom_checks import *
from discord import Game, utils, Embed
from discord.ext.commands import *



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
        self.setup()


    def setup(self):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                self.bot.load_extension(f'cogs.{filename[:-3]}')
                print(f'{filename} cog loaded)')


    def prepare_client(self):

        # Set bot status and reiniitialize database
        @self.bot.event
        async def on_ready():
            await self.bot.change_presence()
            self.add_all_members_to_db()
            print(f'* Connected to Discord as {self.bot.user.name}')


        # Watch for new server members and add them to db
        @self.bot.event
        async def on_member_join(member):
            self.add_member_to_db(member)


        # Channel gated ping command for test-channel channel (must have test role)
        @self.bot.command(name='testchannel-ping')
        @channel_allow(ch_test_channel)
        @has_role(rl_test_role)
        async def testchannel_ping(context):
            await context.send('Success! You\'re in the right place.' )

        @testchannel_ping.error
        async def testchannel_ping_error(context, error):
            if isinstance(error, commands.CheckFailure):
                await context.send('nothing to see here.')


        # Channel gated ping command for general channel
        @self.bot.command(name='general-ping')
        @channel_allow(ch_general)
        async def general_ping(context):
            await context.send('Success! You\'re in the right place.' )

        @general_ping.error
        async def general_ping_error(context, error):
            if isinstance(error, commands.CheckFailure):
                await context.send('nothing to see here.')


        # Channel gated ping command for test-channel AND general channels
        @self.bot.command(name='test-ping')
        @channel_restrict(ch_general, ch_test_channel)
        async def test_ping(context):
            await context.send('Success! You\'re in the right place.' )

        @test_ping.error
        async def testchannel_ping_error(context, error):
            if isinstance(error, commands.CheckFailure):
                await context.send('nothing to see here.')


        # DM only command
        @self.bot.command(name='test-dm')
        @dm_only()
        async def test_dm(context):
            await context.send('Success! You\'re in the right place.' )


        # Announce to other channels
        @self.bot.command(name='announce')
        async def announce(context, channel, *, args):
            channel = context.message.channel_mentions[0]
            await channel.send(args)


        # Movie lookup
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


        # TV show lookup
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


        # Video game lookup
        @self.bot.command(name='videogame')
        async def video_game(context, *args):
            video_game = media(media_type='video_game', title=' '.join(args))
            await context.send(embed=self.build_media_embed(video_game))


        # Manually Add/remove points from user
        @self.bot.command(name='gimmepoints')
        async def gimmepoints(context, arg):
            try:
                point_change = int(arg)
            except Exception as e:
                await context.send('Please pass a number as an argument')
                pass
            self.update_points(context.message.author, point_change)


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
            print(f'Added {member.id} to database')
        except Exception as e:
            print (f'Error adding member: {e}')


    # Get user by member ID
    def get_user(self, member_id):
        try:
            with self.db.cursor() as cursor:
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
    def update_points(self, member, points):
        member_info = self.get_user(member.id)
        with self.db.cursor() as cursor:
            try:
                point_total = member_info["points"] + points
                sql = "UPDATE gt_users SET points=%s WHERE member_id=%s"
                cursor.execute(sql, (point_total, member.id))
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
        return embed


    def run(self):
        self.bot.run(self.token)

if __name__ == '__main__':
    bot = GhoulBot(BOT_TOKEN)
    bot.run()
