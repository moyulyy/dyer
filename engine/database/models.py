# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/database/models.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。
#
# 教学版说明：为防止爬取到的用户个人信息被用于定位真人并私信骚扰，
# 本 ORM 不再持久化任何可识别用户的字段（用户 ID、IP 归属地、头像、
# 主页链接、签名、性别等一律不落库）。原始用户 ID 在提取层经
# tools.user_hash.anonymize_user_id 转为匿名 creator_hash 后写入，
# 仅用于"同一创作者"的内容分组；昵称保留但经 mask_nickname 中间脱敏。
# 创作者个人档案表（XhsCreator/DyCreator/WeiboCreator/TiebaCreator/
# ZhihuCreator/BilibiliUpInfo/BilibiliContactInfo）已整体移除。

from sqlalchemy import create_engine, Column, Integer, Text, String, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


class DouyinAweme(Base):
    __tablename__ = 'douyin_aweme'
    id = Column(Integer, primary_key=True, comment='主键ID')
    creator_hash = Column(String(64), index=True, comment='创作者匿名哈希')
    nickname = Column(Text, comment='用户昵称(已脱敏)')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')
    aweme_id = Column(String(255), index=True, comment='作品ID')
    aweme_type = Column(Text, comment='作品类型')
    title = Column(Text, comment='作品标题')
    desc = Column(Text, comment='作品描述')
    create_time = Column(BigInteger, index=True, comment='创建时间戳')
    liked_count = Column(Text, comment='点赞数')
    comment_count = Column(Text, comment='评论数')
    share_count = Column(Text, comment='分享数')
    collected_count = Column(Text, comment='收藏数')
    aweme_url = Column(Text, comment='作品URL')
    cover_url = Column(Text, comment='封面URL')
    video_download_url = Column(Text, comment='视频下载URL')
    music_download_url = Column(Text, comment='音乐下载URL')
    note_download_url = Column(Text, comment='笔记下载URL')
    source_keyword = Column(Text, default='', comment='来源关键词')

class DouyinAwemeComment(Base):
    __tablename__ = 'douyin_aweme_comment'
    id = Column(Integer, primary_key=True, comment='主键ID')
    creator_hash = Column(String(64), index=True, comment='创作者匿名哈希')
    nickname = Column(Text, comment='用户昵称(已脱敏)')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')
    comment_id = Column(String(255), index=True, comment='评论ID')
    aweme_id = Column(String(255), index=True, comment='作品ID')
    content = Column(Text, comment='评论内容')
    create_time = Column(BigInteger, comment='创建时间戳')
    sub_comment_count = Column(Text, comment='子评论数')
    parent_comment_id = Column(String(255), comment='父评论ID')
    like_count = Column(Text, default='0', comment='点赞数')
    pictures = Column(Text, default='', comment='图片')
