import base64
import json
import re
from datetime import datetime
from typing import List

import aiohttp
from astrbot import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At, Image, Plain, Node, Nodes
from astrbot.core.platform.astr_message_event import AstrMessageEvent


from .preset_manager import PresetManager
from .utils import ImageWorkflow, TableGenerator
from .economy import EconomyManager


class FigurineProPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.data_dir = StarTools.get_data_dir()

        self.preset_manager = PresetManager(self.data_dir)
        self.economy = EconomyManager(self.data_dir, self.conf)
        self.iwf = ImageWorkflow(
            proxy_url=self.conf.get("proxy_url") if self.conf.get("use_proxy") else None,
            timeout=self.conf.get("timeout", 120)
        )

        self.preset_images_dir = self.data_dir / "preset_images"
        self.preset_images_dir.mkdir(parents=True, exist_ok=True)
        self.preset_images_map_file = self.data_dir / "preset_images_map.json"
        self.preset_images_map = {}
        self._load_image_map()

    def _load_image_map(self):
        try:
            if self.preset_images_map_file.exists():
                self.preset_images_map = json.loads(self.preset_images_map_file.read_text(encoding='utf-8'))
        except:
            self.preset_images_map = {}

    def _save_image_map(self):
        try:
            self.preset_images_map_file.write_text(json.dumps(self.preset_images_map, indent=2), encoding='utf-8')
        except:
            pass

    def is_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为配置文件中的管理员"""
        sender = event.get_sender_id()
        admins = self.conf.get("admins_id", [])
        return str(sender) in admins



    async def _call_api(self, image_bytes_list: List[bytes], prompt: str) -> bytes | str:
        """调用 LLM API 生成图片"""
        api_mode = self.conf.get("api_mode", "generic")
        model = self.conf.get("model", "nano-banana")

        payload = {}
        headers = {"Content-Type": "application/json"}
        url = ""

        if api_mode == "gemini_official":
            base_url = self.conf.get("gemini_api_url", "https://generativelanguage.googleapis.com")
            keys = self.conf.get("gemini_api_keys", [])
            if not keys: return "❌ 未配置 Gemini API Key"

            key = keys[0]
            url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent?key={key}"

            parts = [{"text": f"Generate a high quality image based on this description: {prompt}"}]
            for img in image_bytes_list:
                parts.append({
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(img).decode('utf-8')
                    }
                })
            payload = {"contents": [{"parts": parts}]}

        else:
            base_url = self.conf.get("generic_api_url", "https://api.bltcy.ai/v1/chat/completions")
            keys = self.conf.get("generic_api_keys", [])
            if not keys: return "❌ 未配置 Generic API Key"

            key = keys[0]
            url = base_url
            headers["Authorization"] = f"Bearer {key}"

            messages = [
                {"role": "system", "content": "You are an expert AI artist. Output only the image URL. Do not talk."}]

            user_content = [{"type": "text", "text": prompt}]
            for img in image_bytes_list:
                b64 = base64.b64encode(img).decode('utf-8')
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                })
            messages.append({"role": "user", "content": user_content})

            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "max_tokens": 4000
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, proxy=self.iwf.proxy) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        return f"API Error {resp.status}: {text[:200]}"

                    data = await resp.json()

                    img_url = None

                    # 尝试解析 Generic/OpenAI 格式
                    if "choices" in data:
                        content = data["choices"][0]["message"]["content"]
                        # 找 Markdown 图片: ![x](url)
                        match = re.search(r'\!\[.*?\]\((.*?)\)', content)
                        if match:
                            img_url = match.group(1)
                        else:
                            match = re.search(r'https?://[^\s)]+', content)
                            if match: img_url = match.group(0)

                    # 尝试解析 Gemini 格式 (如果是 URL 模式)
                    elif "candidates" in data:
                        try:
                            txt = data["candidates"][0]["content"]["parts"][0]["text"]
                            match = re.search(r'https?://[^\s)]+', txt)
                            if match: img_url = match.group(0)
                        except:
                            pass

                    if not img_url:
                        return f"无法提取图片链接，API响应: {str(data)[:200]}..."

                    return await self.iwf.download_image(img_url) or "❌ 图片下载失败 (连接超时或被拦截)"

        except Exception as e:
            logger.error(f"API Call Failed: {e}")
            return f"系统错误: {e}"



    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_message(self, event: AstrMessageEvent):
        """处理所有消息，匹配预设指令"""
        text = event.message_str.strip()
        if not text: return

        parts = text.split()
        cmd = parts[0]

        # 检查是否命中预设
        prompt_template = self.preset_manager.get_prompt(cmd)
        if not prompt_template: return

        sender_id = event.get_sender_id()

        if str(sender_id) in (self.conf.get("user_blacklist") or []):
            return

        skip_cost = self.is_admin(event)

        if not skip_cost:
            success, msg = await self.economy.check_and_deduct(sender_id, event.get_group_id())
            if not success:
                tip = msg
                if self.conf.get("enable_checkin", False):
                    tip += "\n📅 提示: 发送 #手办化签到 可获取次数"
                yield event.plain_result(f"❌ {tip}")
                return

        yield event.plain_result(f"🎨 收到 [{cmd}] 请求，正在绘图...")

        additional_text = " ".join(parts[1:])
        full_prompt = f"{prompt_template}, {additional_text}" if additional_text else prompt_template

        images = await self.iwf.get_images_from_event(event)

        # 如果不是纯文生图模式(text_only)，且没图，报错
        if not images and "text_only" not in prompt_template:
            yield event.plain_result("⚠️ 请发送一张图片，或引用图片后输入命令。")
            return

        result = await self._call_api(images, full_prompt)

        if isinstance(result, bytes):
            filename = f"{cmd}_{int(datetime.now().timestamp())}.png"
            file_path = self.preset_images_dir / filename
            file_path.write_bytes(result)

            # 更新映射
            self.preset_images_map[cmd] = str(file_path)
            self._save_image_map()

            # 构建回复
            info_text = f"✅ {cmd} 完成"
            if not skip_cost and self.conf.get("enable_user_limit"):
                remain = self.economy.get_user_count(sender_id)
                info_text += f" | 剩余次数: {remain}"

            yield event.chain_result([
                Image.fromBytes(result),
                Plain(info_text)
            ])
        else:
            if not skip_cost:
                # 判断刚才扣的是用户还是群组
                if self.conf.get("enable_user_limit"):
                    # 退还用户
                    await self.economy.admin_add_points(sender_id, 1, is_group=False)
                    logger.info(f"[手办化] 生成失败，已自动退还用户 {sender_id} 1次额度")

                elif self.conf.get("enable_group_limit") and event.get_group_id():
                    # 退还群组
                    await self.economy.admin_add_points(event.get_group_id(), 1, is_group=True)
                    logger.info(f"[手办化] 生成失败，已自动退还群组 {event.get_group_id()} 1次额度")

            yield event.plain_result(f"❌ 生成失败: {result}\n(检测到生成失败，已自动返还扣除的次数)")



    @filter.command("手办化帮助", aliases={"lmhelp", "手办化菜单"})
    async def cmd_help(self, event: AstrMessageEvent):
        """展示插件帮助菜单"""
        presets = self.preset_manager.get_all()
        preset_list_str = "、".join([p[0] for p in presets])

        help_text = (
            "🎨 **手办化插件帮助**\n"
            "━━━━━━━━━━━━━━━\n"
            "**【基础用法】**\n"
            "1. 发送图片 + 命令 (如：[图片] #手办化)\n"
            "2. 引用图片 + 命令\n"
            "3. 命令后可加额外描述 (如：#手办化 红色头发)\n\n"
            "**【可用风格命令】**\n"
            f"{preset_list_str}\n\n"
            "**【其他指令】**\n"
            "• #lm列表 : 查看所有风格预览图\n"
            "• #手办化签到 : 每日领取免费次数\n"
            "• #手办化查询次数 : 查看剩余额度\n"
            "• #手办化帮助 : 显示此菜单"
        )


        if self.is_admin(event):
            help_text += (
                "\n\n**【管理员指令】**\n"
                "• #lm添加 <词>:<提示词> (新增/修改预设)\n"
                "• #lm删除 <词> (删除预设)\n"
                "• #lm查看 <词> (查看提示词源码)\n"
                "• #手办化增加用户次数 <QQ> <数量>"
            )

        # 构建节点消息（合并转发），如果平台不支持会自动降级为文本
        try:
            bot_id = "Robot"
            if hasattr(event, "robot") and event.robot: bot_id = str(event.robot.id)

            node = Node(
                name="手办化助手",
                uin=bot_id,
                content=[Plain(help_text)]
            )
            yield event.chain_result([Nodes(nodes=[node])])
        except:
            yield event.plain_result(help_text)


    @filter.command("手办化签到")
    async def cmd_checkin(self, event: AstrMessageEvent):
        msg = await self.economy.checkin(event.get_sender_id())
        yield event.plain_result(msg)

    @filter.command("手办化查询次数")
    async def cmd_query(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        msg = f"👤 用户剩余: {self.economy.get_user_count(uid)}"
        if gid := event.get_group_id():
            msg += f"\n👥 本群剩余: {self.economy.get_group_count(gid)}"
        yield event.plain_result(msg)

    @filter.command("手办化增加用户次数")
    async def cmd_add_points(self, event: AstrMessageEvent):
        if not self.is_admin(event): return

        # 尝试智能解析：#指令 QQ 数量 或 #指令 @人 数量
        parts = event.message_str.split()
        target = None
        count = None

        nums = [x for x in parts if x.isdigit()]
        if len(nums) >= 2:
            target = nums[1]
            count = int(nums[2]) if len(nums) > 2 else int(nums[1])

        for comp in event.message_obj.message:
            if isinstance(comp, At):
                target = str(comp.qq)
                for n in nums:
                    if str(n) != target: count = int(n)

        if not target and len(nums) >= 2:
            target = nums[0]
            count = int(nums[1])

        if target and count is not None:
            msg = await self.economy.admin_add_points(target, count)
            yield event.plain_result(msg)
        else:
            yield event.plain_result("格式: #手办化增加用户次数 <QQ> <数量> 或 @用户 <数量>")


    @filter.command("lm列表")
    async def lm_list(self, event: AstrMessageEvent):
        """生成预览图"""
        presets = self.preset_manager.get_all()
        if not presets:
            yield event.plain_result("⚠️ 当前没有配置任何预设。")
            return

        yield event.plain_result("🖼️ 正在生成预览列表，请稍候...")

        def get_path(name): return self.preset_images_map.get(name)

        img_data = await TableGenerator.create_preset_table(
            presets, get_path,
            quality=self.conf.get("preset_table_quality", "高清")
        )
        yield event.chain_result([Image.fromBytes(img_data)])

    @filter.command("lm添加")
    async def lm_add(self, event: AstrMessageEvent):
        if not self.is_admin(event): return

        raw = event.message_str.replace("lm添加", "").strip()
        if ":" not in raw:
            yield event.plain_result("格式错误。用法: #lm添加 触发词:提示词英文")
            return

        key, val = raw.split(":", 1)
        self.preset_manager.add_prompt(key.strip(), val.strip())
        yield event.plain_result(f"✅ 已添加/修改预设: 【{key.strip()}】")

    @filter.command("lm删除")
    async def lm_del(self, event: AstrMessageEvent):
        if not self.is_admin(event): return
        key = event.message_str.replace("lm删除", "").strip()
        if self.preset_manager.delete_prompt(key):
            yield event.plain_result(f"🗑️ 已删除预设: 【{key}】")
        else:
            yield event.plain_result(f"❌ 未找到预设: {key}")

    @filter.command("lm查看")
    async def lm_view(self, event: AstrMessageEvent):
        if not self.is_admin(event): return
        key = event.message_str.replace("lm查看", "").strip()
        prompt = self.preset_manager.get_prompt(key)
        if prompt:
            yield event.plain_result(f"🔍 【{key}】 Prompt:\n{prompt}")
        else:
            yield event.plain_result(f"❌ 未找到预设: {key}")