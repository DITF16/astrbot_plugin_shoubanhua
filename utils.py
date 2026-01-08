import io
import asyncio
import base64
import aiohttp
from pathlib import Path
from PIL import Image as PILImage, ImageDraw, ImageFont
from astrbot import logger


class ImageWorkflow:
    def __init__(self, proxy_url: str | None = None, max_retries: int = 3, timeout: int = 60):
        self.proxy = proxy_url
        self.max_retries = max_retries
        self.timeout = timeout

    async def terminate(self):
        pass

    async def download_image(self, url: str) -> bytes | None:
        """通用图片下载"""
        logger.info(f"正在下载图片: {url}")
        for i in range(self.max_retries + 1):
            try:
                import ssl
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                connector = aiohttp.TCPConnector(ssl=ssl_context)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(url, proxy=self.proxy, timeout=self.timeout) as resp:
                        resp.raise_for_status()
                        return await resp.read()
            except Exception as e:
                if i < self.max_retries:
                    await asyncio.sleep(1)
                else:
                    logger.error(f"下载最终失败: {url}, 错误: {e}")
                    return None
        return None

    async def get_images_from_event(self, event) -> list[bytes]:
        """简化的从事件获取图片逻辑"""
        from astrbot.core.message.components import Image, Reply, At

        img_bytes_list = []

        # 1. 检查当前消息和回复链
        msgs = event.message_obj.message
        for seg in msgs:
            if isinstance(seg, Image):  # 直接图片
                if seg.url:
                    img_bytes_list.append(await self.download_image(seg.url))
                elif seg.file:
                    img_bytes_list.append(await self._load_local(seg.file))
            elif isinstance(seg, Reply) and seg.chain:  # 回复中的图片
                for item in seg.chain:
                    if isinstance(item, Image):
                        if item.url: img_bytes_list.append(await self.download_image(item.url))

        # 2. 检查@用户的头像
        # (这里为了代码简洁，仅保留图片下载，如果需要@头像功能可在此恢复原逻辑)

        # 过滤 None
        return [img for img in img_bytes_list if img]

    async def _load_local(self, path: str) -> bytes | None:
        try:
            return await asyncio.to_thread(Path(path).read_bytes)
        except:
            return None


class TableGenerator:
    @staticmethod
    async def create_preset_table(presets: list[tuple[str, str]], image_getter_func, quality="高清", cols=5) -> bytes:
        """生成预设预览图"""
        # 参数配置
        if quality == "标准":
            cell_w, cell_h, img_h, pad, font_sz = 200, 250, 200, 10, 16
        elif quality == "高清":
            cell_w, cell_h, img_h, pad, font_sz = 300, 380, 320, 15, 24
        else:
            cell_w, cell_h, img_h, pad, font_sz = 400, 500, 420, 20, 30

        rows = (len(presets) + cols - 1) // cols
        table_w = cols * cell_w + (cols + 1) * pad
        table_h = rows * cell_h + (rows + 1) * pad

        img = PILImage.new('RGB', (table_w, table_h), 'white')
        draw = ImageDraw.Draw(img)

        # 尝试加载字体
        font = ImageFont.load_default()
        try:
            # 简单尝试几个常见中文字体
            font = ImageFont.truetype("msyh.ttc", font_sz)
        except:
            pass

        for i, (name, prompt) in enumerate(presets):
            row, col = i // cols, i % cols
            x = pad + col * (cell_w + pad)
            y = pad + row * (cell_h + pad)

            # 绘制边框
            draw.rectangle([x, y, x + cell_w, y + cell_h], outline='black', width=1)

            # 尝试获取该预设的预览图 (这里调用传入的回调函数获取本地缓存路径)
            preview_path = image_getter_func(name)

            if preview_path and Path(preview_path).exists():
                try:
                    p_img = PILImage.open(preview_path).convert('RGB')
                    p_img.thumbnail((cell_w - 2 * pad, img_h - 2 * pad), PILImage.Resampling.LANCZOS)
                    # 居中粘贴
                    px = x + (cell_w - p_img.width) // 2
                    py = y + (img_h - p_img.height) // 2
                    img.paste(p_img, (px, py))
                except:
                    pass
            else:
                # 绘制无图占位
                draw.text((x + 20, y + img_h // 2), "No Preview", fill='gray', font=font)

            # 绘制文字
            text_bg_y = y + img_h
            draw.rectangle([x, text_bg_y, x + cell_w, text_bg_y + (cell_h - img_h)], fill='#f0f0f0')
            draw.text((x + 10, text_bg_y + 10), name, fill='black', font=font)

        out = io.BytesIO()
        img.save(out, format='PNG')
        return out.getvalue()