import discord
import os
import asyncio
import json
import threading
import sys
from dotenv import load_dotenv

# --- 全局配置 ---
# 尝试从 .env 文件加载环境变量，方便本地测试
load_dotenv() 
# 从 Railway 的环境变量中获取令牌
TOKEN = os.getenv('BOT_TOKEN')

# 如果未找到令牌，则打印错误并退出
if TOKEN is None:
    print("错误：未找到名为 'BOT_TOKEN' 的环境变量。请在 Railway 的 Variables 中设置它。")
    exit()

# 定义 Railway Volume 的挂载路径，所有持久化数据都将存放在这里
# 这个路径 '/data' 必须与你在 Railway Volume 中设置的 Mount Path 一致。
PERSISTENT_DATA_DIR = "/data"

# --- 持久化设置 ---
# 将配置文件路径指向持久化目录
SETTINGS_FILE = os.path.join(PERSISTENT_DATA_DIR, "bot_settings.json")
authorized_user_ids = set()
SEND_DELAY = 1.0  # 默认发送延迟为1秒
MIN_DELAY = 0.25  # 最小延迟限制，防止滥用

# 线程锁，用于安全地多线程读写文件
file_lock = threading.Lock()

def load_settings():
    """在机器人启动时加载所有设置（授权用户和发送延迟）"""
    global authorized_user_ids, SEND_DELAY
    with file_lock:
        # 确保持久化目录存在，如果不存在则创建它
        os.makedirs(PERSISTENT_DATA_DIR, exist_ok=True)
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    data = json.load(f)
                    authorized_user_ids = set(data.get("user_ids", []))
                    SEND_DELAY = data.get("send_delay", 1.0)
                    print(f"成功加载 {len(authorized_user_ids)} 个授权用户。")
                    print(f"当前发送延迟设置为: {SEND_DELAY} 秒。")
            else:
                # 如果配置文件不存在，则使用默认值创建它
                save_settings()
                print("未找到设置文件，已使用默认值创建。")
        except (json.JSONDecodeError, IOError) as e:
            print(f"警告：加载设置文件失败，将使用默认值。错误: {e}")
            authorized_user_ids = set()
            SEND_DELAY = 1.0

def save_settings():
    """将所有当前设置（权限和速度）保存到文件"""
    with file_lock:
        try:
            settings_data = {
                "user_ids": list(authorized_user_ids),
                "send_delay": SEND_DELAY
            }
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings_data, f, indent=4)
        except IOError as e:
            print(f"严重错误：无法保存设置文件！错误: {e}")

def console_input_loop():
    """一个在后台运行的独立线程，用于监听和处理来自 Railway 控制台的输入"""
    global SEND_DELAY
    print("\n控制台管理已启动。输入 'help' 查看可用命令。")
    while True:
        try:
            command = input()
            parts = command.strip().split()
            if not parts: continue
            cmd = parts[0].lower()
            
            if cmd == "add_user":
                if len(parts) > 1 and parts[1].isdigit():
                    user_id = parts[1]
                    if user_id not in authorized_user_ids:
                        authorized_user_ids.add(user_id)
                        save_settings()
                        print(f"[控制台] 成功添加用户ID: {user_id}")
                    else: print(f"[控制台] 用户ID: {user_id} 已在列表中。")
                else: print("[控制台] 用法: add_user <用户ID>")

            elif cmd == "remove_user":
                if len(parts) > 1 and parts[1].isdigit():
                    user_id = parts[1]
                    if user_id in authorized_user_ids:
                        authorized_user_ids.remove(user_id)
                        save_settings()
                        print(f"[控制台] 成功移除用户ID: {user_id}")
                    else: print(f"[控制台] 用户ID: {user_id} 不在列表中。")
                else: print("[控制台] 用法: remove_user <用户ID>")
            
            elif cmd == "set_speed":
                if len(parts) > 1:
                    try:
                        new_delay = float(parts[1])
                        if new_delay >= MIN_DELAY:
                            SEND_DELAY = new_delay
                            save_settings()
                            print(f"[控制台] 发送延迟已成功更新为: {SEND_DELAY} 秒。")
                        else: print(f"[控制台] 错误：延迟不能低于 {MIN_DELAY} 秒。")
                    except ValueError: print("[控制台] 错误：请输入一个有效的数字。")
                else: print("[控制台] 用法: set_speed <秒数>")

            elif cmd == "status":
                print(f"\n--- 机器人当前状态 ---\n发送延迟: {SEND_DELAY} 秒\n已授权用户数: {len(authorized_user_ids)}\n-----------------------\n")

            elif cmd == "list_users":
                print("[控制台] 当前授权的用户ID列表:")
                if authorized_user_ids:
                    for user_id in authorized_user_ids: print(f"- {user_id}")
                else: print("- (列表为空)")

            elif cmd == "help":
                print("\n--- 控制台命令帮助 ---\nadd_user <用户ID>    - 添加授权用户\nremove_user <用户ID> - 移除授权用户\nlist_users           - 显示所有授权用户\nset_speed <秒数>     - 设置消息发送间隔\nstatus               - 查看机器人当前设置状态\n-----------------------\n")
            
            else:
                if not command.startswith('['): print(f"[控制台] 未知命令: '{cmd}'. 输入 'help' 查看可用命令。")
        except Exception as e: print(f"[控制台] 处理命令时发生错误: {e}")

# --- 机器人设置 ---
intents = discord.Intents.default()
bot = discord.Bot(intents=intents)
MAX_SEND_COUNT = 10 

# --- 确认UI界面 ---
class ConfirmationView(discord.ui.View):
    def __init__(self, author, target_user, message_content, count):
        super().__init__(timeout=60.0)
        self.author = author
        self.target_user = target_user
        self.message_content = message_content
        self.count = count
        self.interaction = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("这不是你的操作面板！", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="我同意并发送", style=discord.ButtonStyle.danger)
    async def agree_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.interaction = interaction
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content=f"✅ **已同意**！准备向 {self.target_user.mention} 发送 {self.count} 条消息...", view=self)
        await self.start_sending()
    
    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="❌ **操作已取消。**", view=self)
        self.stop()

    async def start_sending(self):
        sent_count, failed_count = 0, 0
        for i in range(self.count):
            try:
                await self.target_user.send(self.message_content)
                sent_count += 1
                await asyncio.sleep(SEND_DELAY)
            except discord.HTTPException as e:
                if e.status == 429:
                    print("="*60 + "\n  [严重警告] 机器人被Discord API速率限制 (429)！\n  请立即使用 'set_speed' 命令增加延迟，或等待限制解除！\n" + "="*60)
                    await self.interaction.followup.send(f"⚠️ **任务中止！** 机器人被速率限制。", ephemeral=True)
                else: print(f"发送时发生HTTP错误 (状态码: {e.status}): {e.text}")
                failed_count = self.count - i; break
            except discord.Forbidden:
                await self.interaction.followup.send(f"⚠️ **发送失败！** 无法向 {self.target_user.mention} 发送私信。", ephemeral=True)
                failed_count = self.count - i; break
            except Exception as e:
                print(f"发送时发生未知错误: {e}")
                failed_count = self.count - i; break
        await self.interaction.followup.send(f"📬 **发送任务报告**\n\n- **目标用户**: {self.target_user.mention}\n- **成功发送**: {sent_count} 次\n- **失败**: {failed_count} 次", ephemeral=True)
        self.stop()

# --- 斜杠命令定义 ---
@bot.slash_command(name="私信", description="向指定用户发送私信（需要授权）")
async def private_message(
    ctx: discord.ApplicationContext,
    目标用户: discord.Option(discord.Member, "选择要发送私信的用户", required=True),
    消息内容: discord.Option(str, "要发送的消息内容 (不填则发送默认问候)", required=False, default="你好！你有一条来自服务器的新消息。"),
    发送次数: discord.Option(int, f"消息要发送的次数 (默认为1, 最大为{MAX_SEND_COUNT})", required=False, default=1)
):
    # 在控制台记录命令使用来源
    print(f"--- [命令日志] ---\n用户: {ctx.author} ({ctx.author.id})\n服务器: {ctx.guild.name if ctx.guild else '私信'}\n目标: {目标用户} | 次数: {发送次数}\n-----------------")
    
    # 权限检查
    if str(ctx.author.id) not in authorized_user_ids:
        await ctx.respond("❌ **无权使用**：你没有使用此命令的权限。", ephemeral=True); return
    
    # 次数检查
    if not (1 <= 发送次数 <= MAX_SEND_COUNT):
        await ctx.respond(f"❌ **错误**：发送次数必须在 1 到 {MAX_SEND_COUNT} 之间。", ephemeral=True); return
    
    # 单次发送逻辑
    if 发送次数 == 1:
        try:
            await 目标用户.send(消息内容)
            await ctx.respond(f"✅ 成功向 {目标用户.mention} 发送了一条私信。", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"❌ **发送失败**: {e}", ephemeral=True)
    # 多次发送逻辑
    else:
        view = ConfirmationView(ctx.author, 目标用户, 消息内容, 发送次数)
        embed = discord.Embed(
            title="⚠️ **连续发送警告** ⚠️",
            description=f"你即将向 **{目标用户.name}** 连续发送 **{发送次数}** 次消息。",
            color=discord.Color.red()
        ).add_field(
            name="免责声明",
            value="所有因使用此功能而产生的后果和责任，均由你个人承担。"
        ).set_footer(
            text="60秒后操作将自动取消。"
        )
        await ctx.respond(embed=embed, view=view, ephemeral=True)

# --- 机器人启动事件 ---
@bot.event
async def on_ready():
    print(f'------------------------------------\n机器人 {bot.user} 已成功登录！\n------------------------------------')

# --- 启动所有服务 ---
if __name__ == "__main__":
    # 1. 加载所有持久化设置
    load_settings()

    # 2. 启动后台控制台监听线程
    console_thread = threading.Thread(target=console_input_loop, daemon=True)
    console_thread.start()

    # 3. 启动Discord机器人主程序
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        print("错误：登录失败。请检查你的 'BOT_TOKEN' 环境变量。")
    except Exception as e:
        print(f"机器人运行时发生致命错误: {e}")
