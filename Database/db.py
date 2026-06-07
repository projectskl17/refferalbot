from pymongo import MongoClient
import config


class Database:
    def __init__(self):
        self.client = MongoClient(config.MONGO_URI)
        self.db = self.client[config.DB_NAME]

        self.users = self.db.users
        self.chats = self.db.chats
        self.connected_chats = self.db.connected_chats
        self.members = self.db.members
        self.referrals = self.db.referrals

    def add_user(self, user_id):
        self.users.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"user_id": user_id}},
            upsert=True,
        )

    def get_user(self, user_id):
        return self.users.find_one({"user_id": user_id})

    def set_user(self, user_id, data):
        self.users.update_one(
            {"user_id": user_id},
            {"$set": data},
            upsert=True,
        )

    def delete_user(self, user_id):
        self.users.delete_one({"user_id": user_id})

    def add_chat(self, chat_id, title=""):
        self.chats.update_one(
            {"chat_id": chat_id},
            {
                "$setOnInsert": {
                    "chat_id": chat_id,
                    "title": title,
                }
            },
            upsert=True,
        )

    def get_chat(self, chat_id):
        return self.chats.find_one({"chat_id": chat_id})

    def set_chat(self, chat_id, data):
        self.chats.update_one(
            {"chat_id": chat_id},
            {"$set": data},
            upsert=True,
        )

    def delete_chat(self, chat_id):
        self.chats.delete_one({"chat_id": chat_id})

    def connect_chat(self, chat_id, title, invite_link, settings=None):
        default_settings = {
            "ban_enabled": True,
            "ban_after_seconds": 86400,
            "referral_count": 3,
        }
        if settings:
            default_settings.update(settings)
        self.connected_chats.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "title": title,
                    "invite_link": invite_link,
                    "settings": default_settings,
                }
            },
            upsert=True,
        )

    def get_connected_chat(self, chat_id):
        return self.connected_chats.find_one({"chat_id": chat_id})

    def get_all_connected_chats(self):
        return list(self.connected_chats.find())

    def update_chat_settings(self, chat_id, settings):
        self.connected_chats.update_one(
            {"chat_id": chat_id},
            {"$set": {"settings": settings}},
        )

    def add_member(self, chat_id, user_id, invite_link, deadline_ts):
        self.members.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "invite_link": invite_link,
                    "deadline_ts": deadline_ts,
                    "completed": False,
                    "banned": False,
                    "join_msg_id": None,
                }
            },
            upsert=True,
        )

    def get_member(self, chat_id, user_id):
        return self.members.find_one({"chat_id": chat_id, "user_id": user_id})

    def set_member(self, chat_id, user_id, data):
        self.members.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$set": data},
        )

    def get_pending_members(self, chat_id):
        return list(self.members.find({"chat_id": chat_id, "completed": False, "banned": False}))

    def add_referral(self, referrer_id, referred_id, chat_id):
        self.referrals.insert_one({
            "referrer_id": referrer_id,
            "referred_id": referred_id,
            "chat_id": chat_id,
        })

    def count_referrals(self, referrer_id, chat_id):
        return self.referrals.count_documents({"referrer_id": referrer_id, "chat_id": chat_id})


db = Database()