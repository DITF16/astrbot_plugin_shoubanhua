import json
import random
import asyncio
from datetime import datetime
from pathlib import Path
from astrbot.core import AstrBotConfig
from astrbot import logger


class EconomyManager:
    def __init__(self, data_dir: Path, config: AstrBotConfig):
        self.data_dir = data_dir
        self.conf = config

        # 文件路径
        self.user_counts_file = self.data_dir / "user_counts.json"
        self.group_counts_file = self.data_dir / "group_counts.json"
        self.user_checkin_file = self.data_dir / "user_checkin.json"

        # 内存缓存
        self.user_counts = {}
        self.group_counts = {}
        self.user_checkin_data = {}

        self._load_all()

    def _load_all(self):
        """加载所有数据"""
        self.user_counts = self._load_json(self.user_counts_file)
        self.group_counts = self._load_json(self.group_counts_file)
        self.user_checkin_data = self._load_json(self.user_checkin_file)

    def _load_json(self, path: Path) -> dict:
        if not path.exists(): return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except:
            return {}

    async def _save_json(self, path: Path, data: dict):
        try:
            await asyncio.to_thread(path.write_text, json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存数据失败 {path}: {e}")

    # --- 对外接口 ---

    def get_user_count(self, user_id: str) -> int:
        return self.user_counts.get(str(user_id), 0)

    def get_group_count(self, group_id: str) -> int:
        return self.group_counts.get(str(group_id), 0)

    async def check_and_deduct(self, user_id: str, group_id: str = None) -> tuple[bool, str]:
        """检查并扣除次数。返回: (是否成功, 提示信息)"""
        uid = str(user_id)
        gid = str(group_id) if group_id else None

        # 1. 检查开关
        enable_user_limit = self.conf.get("enable_user_limit", True)
        enable_group_limit = self.conf.get("enable_group_limit", False)

        # 如果都没开限制，直接通过
        if not enable_user_limit and not enable_group_limit:
            return True, "无限制模式"

        cost = 1  # 默认消耗1次，如果支持强力模式这里可变

        # 2. 扣费逻辑
        # 优先扣用户，如果开启了群限制且支持回退，逻辑会比较复杂。
        # 这里简化为：优先看用户限制，再看群限制。

        deducted = False
        source = ""

        # 检查群
        if gid and enable_group_limit:
            g_cnt = self.group_counts.get(gid, 0)
            if g_cnt < cost:
                # 群次数不够，且开启了群限制 -> 失败 (除非后续逻辑允许混合)
                return False, f"本群剩余次数不足 ({g_cnt}次)"

        # 检查个人
        if enable_user_limit:
            u_cnt = self.user_counts.get(uid, 0)
            if u_cnt >= cost:
                self.user_counts[uid] = u_cnt - cost
                deducted = True
                source = "user"
            else:
                # 个人不够，看看能不能扣群的 (如果有这个逻辑需求)
                if gid and enable_group_limit:
                    g_cnt = self.group_counts.get(gid, 0)
                    if g_cnt >= cost:
                        self.group_counts[gid] = g_cnt - cost
                        deducted = True
                        source = "group"
                    else:
                        return False, f"您的次数不足 ({u_cnt})，且群次数也不足 ({g_cnt})"
                else:
                    return False, f"您的次数不足 ({u_cnt})"
        elif gid and enable_group_limit:
            # 没开个人限制，只开群限制
            g_cnt = self.group_counts.get(gid, 0)
            if g_cnt >= cost:
                self.group_counts[gid] = g_cnt - cost
                deducted = True
                source = "group"
            else:
                return False, f"本群次数不足"

        # 保存变更
        if deducted:
            if source == "user": await self._save_json(self.user_counts_file, self.user_counts)
            if source == "group": await self._save_json(self.group_counts_file, self.group_counts)
            return True, "success"

        return True, "未开启限制"  # 兜底

    async def checkin(self, user_id: str) -> str:
        """用户签到"""
        if not self.conf.get("enable_checkin", False):
            return "❌ 签到功能未开启。"

        uid = str(user_id)
        today = datetime.now().strftime("%Y-%m-%d")

        if self.user_checkin_data.get(uid) == today:
            curr = self.user_counts.get(uid, 0)
            return f"📅 您今天已经签到过了。剩余次数: {curr}"

        # 计算奖励
        reward = int(self.conf.get("checkin_fixed_reward", 3))
        if self.conf.get("enable_random_checkin", False):
            max_r = int(self.conf.get("checkin_random_reward_max", 5))
            reward = random.randint(1, max(1, max_r))

        # 发放奖励
        current = self.user_counts.get(uid, 0)
        self.user_counts[uid] = current + reward
        self.user_checkin_data[uid] = today

        await self._save_json(self.user_counts_file, self.user_counts)
        await self._save_json(self.user_checkin_file, self.user_checkin_data)

        return f"🎉 签到成功！获得 {reward} 次。\n当前剩余: {self.user_counts[uid]}"

    async def admin_add_points(self, target_id: str, count: int, is_group: bool = False) -> str:
        """管理员加分"""
        tid = str(target_id)
        if is_group:
            curr = self.group_counts.get(tid, 0)
            self.group_counts[tid] = curr + count
            await self._save_json(self.group_counts_file, self.group_counts)
            return f"✅ 已为群 {tid} 增加 {count} 次 (当前: {self.group_counts[tid]})"
        else:
            curr = self.user_counts.get(tid, 0)
            self.user_counts[tid] = curr + count
            await self._save_json(self.user_counts_file, self.user_counts)
            return f"✅ 已为用户 {tid} 增加 {count} 次 (当前: {self.user_counts[tid]})"