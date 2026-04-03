import discord
from discord.ext import commands
import os
import aiosqlite
import asyncio
import time

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

DB_NAME = "bot.db"

# In-memory rejoin cooldowns: {user_id: unix_timestamp_until_allowed}
cooldowns = {}

# =========================
# CONFIG
# =========================
config = {
    "log_channel": None,
    "category": None,
    "male_role": None,
    "female_role": None,
    "unverified_role": None,
    "staff_role": None
}

# =========================
# DATABASE INIT
# =========================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS requirements (
            gender TEXT PRIMARY KEY,
            text TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        """)
        await db.commit()

# =========================
# DB HELPERS
# =========================
async def add_blacklist(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO blacklist (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_blacklist(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM blacklist WHERE user_id=?", (user_id,))
        await db.commit()

async def is_blacklisted(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM blacklist WHERE user_id=?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def save_config():
    async with aiosqlite.connect(DB_NAME) as db:
        for key, value in config.items():
            if value is not None:
                await db.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                    (key, value)
                )
        await db.commit()

async def load_config():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT key, value FROM config") as cursor:
            rows = await cursor.fetchall()
            for key, value in rows:
                if key in config:
                    config[key] = value

async def set_requirement(gender, text):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO requirements (gender, text) VALUES (?, ?)",
            (gender, text)
        )
        await db.commit()

async def get_requirement(gender):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text FROM requirements WHERE gender=?",
            (gender,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "Not set"

# =========================
# LOGGING
# =========================
async def log_action(guild, message):
    ch = guild.get_channel(config["log_channel"])
    if ch:
        await ch.send(message)

# =========================
# SETUP COMMAND
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild = ctx.guild

    male = await guild.create_role(name="Male", color=discord.Color.blue())
    female = await guild.create_role(name="Female", color=discord.Color.from_rgb(255, 105, 180))
    unverified = await guild.create_role(name="Unverified", color=discord.Color.light_grey())
    staff = await guild.create_role(name="Staff", color=discord.Color.gold())

    category = await guild.create_category(
        "Verification Tickets",
        overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    )

    log_channel = await guild.create_text_channel("verification-logs")
    await log_channel.set_permissions(guild.default_role, view_channel=False)

    admin_role = discord.utils.get(guild.roles, permissions__administrator=True)
    if admin_role:
        await log_channel.set_permissions(admin_role, view_channel=True)

    # Lock all existing channels so @everyone sees nothing
    for channel in guild.channels:
        try:
            await channel.set_permissions(
                guild.default_role,
                view_channel=False
            )
        except:
            pass

    # Allow main channels for verified roles (male/female) AFTER verification
    for channel in guild.text_channels:
        if channel == log_channel or channel.category == category:
            continue
        try:
            await channel.set_permissions(male, view_channel=True)
            await channel.set_permissions(female, view_channel=True)
        except:
            pass

    config.update({
        "log_channel": log_channel.id,
        "category": category.id,
        "male_role": male.id,
        "female_role": female.id,
        "unverified_role": unverified.id,
        "staff_role": staff.id
    })

    await save_config()
    await ctx.send("✅ Setup complete")

# =========================
# REQUIREMENTS COMMAND
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def requirements(ctx, gender, *, text):
    gender = gender.lower()
    await set_requirement(gender, text)
    await ctx.send(f"✅ Requirement set for {gender}")

# =========================
# UNBLACKLIST
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def unblacklist(ctx, user_id: int):
    await remove_blacklist(user_id)
    await ctx.send(f"✅ Unblacklisted {user_id}")

# =========================
# GENDER UI
# =========================
class GenderButtons(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    async def interaction_check(self, interaction):
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Male", style=discord.ButtonStyle.primary)
    async def male(self, interaction, button):
        await self.handle(interaction, "male")

    @discord.ui.button(label="Female", style=discord.ButtonStyle.danger)
    async def female(self, interaction, button):
        await self.handle(interaction, "female")

    async def handle(self, interaction, gender):
        req = await get_requirement(gender)

        embed = discord.Embed(
            title="Requirements",
            description=f"**{gender.capitalize()}**\n\n{req}",
            color=0x5865F2
        )

        await interaction.response.send_message(embed=embed)

        # Gender-specific NEXT STEPS embed
        if gender == "female":
            next_steps_embed = discord.Embed(
                title="Wait — we're not done yet!",
                description=(
                    "**Verification Requirement**\n"
                    "• Submit a short voice note confirming your identity\n\n"
                    "**Important Notice**\n"
                    "The buttons below are restricted and can only be used by authorized administrators."
                ),
                color=0x2b2d31
            )
        else:
            next_steps_embed = discord.Embed(
                title="Wait — we're not done yet!",
                description=(
                    "**Verification Requirement**\n"
                    "POF $1000.00 USD\n"
                    "•Invite 3 girls to the server\n\n"
                    "**Important Notice**\n"
                    "The buttons below are restricted and can only be used by authorized administrators."
                ),
                color=0x2b2d31
            )

        next_steps_embed.set_author(name="NEXT STEPS")

        await interaction.channel.send(embed=next_steps_embed)

        await interaction.channel.send(
            "🎫 Staff Controls:",
            view=TicketControls(self.user_id, gender)
        )

# =========================
# TICKET CONTROLS
# =========================
class TicketControls(discord.ui.View):
    def __init__(self, user_id, gender):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.gender = gender

    def is_staff(self, interaction):
        staff_role = interaction.guild.get_role(config["staff_role"])
        return interaction.user.guild_permissions.administrator or staff_role in interaction.user.roles

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("Staff only", ephemeral=True)

        member = interaction.guild.get_member(self.user_id)

        male = interaction.guild.get_role(config["male_role"])
        female = interaction.guild.get_role(config["female_role"])
        unverified = interaction.guild.get_role(config["unverified_role"])

        await member.remove_roles(unverified)

        role = male if self.gender == "male" else female
        await member.add_roles(role)

        try:
            await member.send("✅ You have been approved and verified.")
        except:
            pass

        await log_action(interaction.guild, f"Approved {member}")
        await interaction.response.send_message("Approved")
        await interaction.channel.delete()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("Staff only", ephemeral=True)

        member = interaction.guild.get_member(self.user_id)

        try:
            await member.send("❌ Your verification was denied.")
        except:
            pass

        await member.kick(reason="Denied")
        await log_action(interaction.guild, f"Denied {member}")

        await interaction.response.send_message("Denied")
        await interaction.channel.delete()

    @discord.ui.button(label="Blacklist", style=discord.ButtonStyle.secondary)
    async def blacklist(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("Staff only", ephemeral=True)

        member = interaction.guild.get_member(self.user_id)

        await add_blacklist(member.id)

        try:
            await member.send("🚫 You have been blacklisted from this server.")
        except:
            pass

        await member.kick(reason="Blacklisted")
        await log_action(interaction.guild, f"Blacklisted {member}")

        await interaction.response.send_message("Blacklisted")
        await interaction.channel.delete()

    @discord.ui.button(label="Add Note", style=discord.ButtonStyle.secondary)
    async def add_note(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("Staff only", ephemeral=True)

        await interaction.response.send_message("✏️ Please type your note in this channel.", ephemeral=True)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await interaction.client.wait_for("message", check=check, timeout=300)
        except asyncio.TimeoutError:
            return

        member = interaction.guild.get_member(self.user_id)
        await log_action(interaction.guild, f"Note on {member}: {msg.content}")

    @discord.ui.button(label="Request Proof", style=discord.ButtonStyle.primary)
    async def request_proof(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("Staff only", ephemeral=True)

        member = interaction.guild.get_member(self.user_id)
        try:
            await member.send(
                "📎 Please provide any required proof or screenshots by replying here or uploading them in your ticket channel."
            )
        except:
            pass

        await interaction.response.send_message("Requested proof from user.", ephemeral=True)

# =========================
# AUTO-KICK TASK
# =========================
async def auto_kick_if_unverified(member_id, guild_id, delay=600):
    await asyncio.sleep(delay)
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    member = guild.get_member(member_id)
    if not member:
        return

    unverified_role = guild.get_role(config["unverified_role"])
    if unverified_role in member.roles:
        try:
            await member.send("⏰ You did not complete verification in time and were removed from the server.")
        except:
            pass
        await member.kick(reason="Verification timeout")
        await log_action(guild, f"Auto-kicked {member} for verification timeout")

        # Add short cooldown (e.g., 10 minutes) to prevent instant rejoin spam
        cooldowns[member.id] = time.time() + 600

# =========================
# MEMBER JOIN
# =========================
async def ensure_config(guild):
    if any(v is None for v in config.values()):
        male = discord.utils.get(guild.roles, name="Male")
        female = discord.utils.get(guild.roles, name="Female")
        unverified = discord.utils.get(guild.roles, name="Unverified")
        staff = discord.utils.get(guild.roles, name="Staff")
        category = discord.utils.get(guild.categories, name="Verification Tickets")
        log_channel = discord.utils.get(guild.text_channels, name="verification-logs")

        if all([male, female, unverified, staff, category, log_channel]):
            config.update({
                "log_channel": log_channel.id,
                "category": category.id,
                "male_role": male.id,
                "female_role": female.id,
                "unverified_role": unverified.id,
                "staff_role": staff.id
            })
            await save_config()

@bot.event
async def on_member_join(member):
    await ensure_config(member.guild)

    if any(v is None for v in config.values()):
        print(f"Config not set up for guild {member.guild.name}, skipping member join.")
        return

    # Anti-rejoin cooldown
    if member.id in cooldowns and time.time() < cooldowns[member.id]:
        try:
            await member.send("⏳ You recently left and cannot rejoin yet. Please try again later.")
        except:
            pass
        await member.kick(reason="Rejoin cooldown")
        return

    if await is_blacklisted(member.id):
        await member.kick(reason="Blacklisted")
        return

    guild = member.guild

    unverified = guild.get_role(config["unverified_role"])
    await member.add_roles(unverified)

    category = guild.get_channel(config["category"])
    staff_role = guild.get_role(config["staff_role"])

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),

        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        ),

        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        ),

        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True
        )
    }

    channel = await guild.create_text_channel(
        f"verify-{member.name}",
        category=category,
        overwrites=overwrites
    )

    embed = discord.Embed(
        title="WELCOME TO THE SERVER",
        description=(
            "Welcome to the server. Before accessing the main sections, you must complete our screening verification.\n\n"
            "**Step 1:** Select your gender below.\n"
            "**Step 2:** Tell us how you were invited.\n"
            "**Step 3:** Wait for our higher-ups to review your screening.\n\n"
            "⚠️ Verification must be completed in 10 minutes or you will be kicked from the server."
        ),
        color=0x2b2d31
    )

    await channel.send(member.mention, embed=embed, view=GenderButtons(member.id))

    await channel.send(
        "📝 **Question:** whats your alias?\n"
        "Please answer in this channel."
    )

    # Start auto-kick timer
    asyncio.create_task(auto_kick_if_unverified(member.id, guild.id, delay=600))

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await init_db()
    await load_config()

    # If config is missing, auto-detect from the guild
    for guild in bot.guilds:
        await ensure_config(guild)

    print(f"Logged in as {bot.user}")
    print(f"Config: {config}")

# =========================
bot.run(os.getenv("TOKEN"))
