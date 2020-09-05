from settings import *
from discord.ext import commands

#Checks channels APPROVED for this command
def channel_allow(*args):
    async def predicate(context):
        msg_approved = False
        current_channel = context.message.channel.id
        for apporved_channel in args:
            if current_channel == apporved_channel:
                msg_approved = True
                break
        return msg_approved == True;
    return commands.check(predicate)


#Checks channels NOT APPROVED for this command
def channel_restrict(*args):
    async def predicate(context):
        msg_approved = True
        current_channel = context.message.channel.id
        for restricted_channel in args:
            if current_channel == restricted_channel:
                msg_approved = False
        return msg_approved == True;
    return commands.check(predicate)

'''
#Checks roles APPROVED to use this command
def role_allow(*args, **kwargs):
    async def predicate(context):
        msg_approved = False
        user_roles = context.message.author.roles
        print(user_roles)
        for role in user_roles:
            for approved_role in args:
                print(role.id)
                print(approved_role)
                if role.id == approved_role:
                    print('Approved')
                    msg_approved = True
                    pass
                else:
                    print('Not Approved')
        return msg_approved == True;
    return commands.check(predicate)

#Checks roles NOT APPROVED to use this command
def role_restrict(*args, **kwargs):
    async def predicate(context):
        msg_approved = True
        user_roles = context.message.author.roles
        print(user_roles)
        for role in user_roles:
            for restricted_role in args:
                if role.id == restricted_role:
                    msg_approved = False
                    pass
        return msg_approved == True;
    return commands.check(predicate)
'''
