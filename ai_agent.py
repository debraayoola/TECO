"""
cogs/ai_agent.py
The "AI" half of the co-admin: chat, toxicity analysis, summarization,
and @mention conversational agent — all powered by Google Gemini
(via a Google AI Studio API key).
"""

import discord
from discord.ext import commands
from discord import app_commands
from collections import defaultdict, deque
from utils.gemini_client import GeminiClient


class AIAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.ai = GeminiClient()
        # Per-user rolling conversation context (in-memory, last 6 turns)
        self.conversations: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))

    # ─── /ask — direct AI question ─────────────────────────────────────────────

    @app_commands.command(name='ask', description='Ask the AI co-admin a question')
    @app_commands.describe(question='What do you want to ask?')
    async def ask(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        history = self.conversations[interaction.user.id]
        answer = await self.ai.chat(question, context=list(history))

        history.append({'role': 'user', 'content': question})
        history.append({'role': 'assistant', 'content': answer})

        e = discord.Embed(
            title='🤖 AI Co-Admin',
            description=answer,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )
        e.set_footer(text=f'Asked by {interaction.user}')
        await interaction.followup.send(embed=e)

    # ─── /clearcontext ──────────────────────────────────────────────────────────

    @app_commands.command(name='clearcontext', description='Clear your AI conversation memory')
    async def clearcontext(self, interaction: discord.Interaction):
        self.conversations[interaction.user.id].clear()
        await interaction.response.send_message(
            embed=discord.Embed(description='🧹 Your AI conversation context has been cleared.',
                                color=discord.Color.green()), ephemeral=True)

    # ─── /toxcheck — manual toxicity check ─────────────────────────────────────

    @app_commands.command(name='toxcheck', description='Check a message for toxicity using AI')
    @app_commands.describe(text='Text to analyze')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def toxcheck(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(ephemeral=True)
        result = await self.ai.check_toxicity(text)

        color = discord.Color.red() if result['is_toxic'] else discord.Color.green()
        e = discord.Embed(
            title='🔍 Toxicity Analysis',
            color=color,
            timestamp=discord.utils.utcnow()
        )
        e.add_field(name='Text', value=f'```{text[:500]}```', inline=False)
        e.add_field(name='Classification', value=result['label'], inline=True)
        e.add_field(name='Confidence', value=f"{result['score'] * 100:.1f}%", inline=True)
        e.add_field(name='Verdict', value='⚠️ Toxic' if result['is_toxic'] else '✅ Clean', inline=True)
        if result.get('reason'):
            e.add_field(name='Reason', value=result['reason'], inline=False)
        await interaction.followup.send(embed=e, ephemeral=True)

    # ─── /summarize — summarize recent channel activity ────────────────────────

    @app_commands.command(name='summarize', description='AI-summarize recent messages in this channel')
    @app_commands.describe(amount='Number of recent messages to summarize (max 100)')
    async def summarize(self, interaction: discord.Interaction, amount: int = 30):
        amount = max(5, min(100, amount))
        await interaction.response.defer()

        messages = [m async for m in interaction.channel.history(limit=amount)]
        messages.reverse()
        text_block = '\n'.join(f'{m.author.display_name}: {m.content}'
                               for m in messages if m.content and not m.author.bot)

        if not text_block.strip():
            return await interaction.followup.send(
                embed=discord.Embed(description='⚠️ No text content found to summarize.',
                                    color=discord.Color.red()))

        summary = await self.ai.summarize(text_block)
        e = discord.Embed(
            title=f'📋 Summary of Last {amount} Messages',
            description=summary,
            color=discord.Color.teal(),
            timestamp=discord.utils.utcnow()
        )
        e.set_footer(text=f'Channel: #{interaction.channel.name}')
        await interaction.followup.send(embed=e)

    # ─── /moderate — AI judges if text breaks rules ────────────────────────────

    @app_commands.command(name='moderate', description='Ask the AI if a message would violate server rules')
    @app_commands.describe(text='Text to evaluate')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def moderate(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(ephemeral=True)
        verdict = await self.ai.moderation_verdict(text)
        tox = await self.ai.check_toxicity(text)

        e = discord.Embed(title='⚖️ AI Moderation Review', color=discord.Color.gold(),
                          timestamp=discord.utils.utcnow())
        e.add_field(name='Message', value=f'```{text[:500]}```', inline=False)
        e.add_field(name='AI Verdict', value=verdict, inline=False)
        e.add_field(name='Toxicity Score', value=f"{tox['label']} ({tox['score']*100:.1f}%)", inline=False)
        await interaction.followup.send(embed=e, ephemeral=True)

    # ─── @mention conversational handler ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if self.bot.user not in message.mentions:
            return

        content = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        content = content.replace(f'<@!{self.bot.user.id}>', '').strip()
        if not content:
            return await message.reply("Hi! Ask me something, e.g. `@bot what's the rule on spam?`")

        async with message.channel.typing():
            history = self.conversations[message.author.id]
            answer = await self.ai.chat(content, context=list(history))
            history.append({'role': 'user', 'content': content})
            history.append({'role': 'assistant', 'content': answer})

        await message.reply(answer[:2000], mention_author=False)

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=discord.Embed(description='❌ Missing permissions for this command.',
                                    color=discord.Color.red()), ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(AIAgent(bot))
