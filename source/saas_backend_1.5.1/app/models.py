from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    # Флаг администратора. По умолчанию обычный пользователь.
    is_admin = Column(Boolean, default=False, nullable=False)
    
    forms = relationship("YandexForm", back_populates="owner", cascade="all, delete-orphan")
    bots = relationship("VkBot", back_populates="owner", cascade="all, delete-orphan")
    routes = relationship("Route", back_populates="owner", cascade="all, delete-orphan")

class YandexForm(Base):
    __tablename__ = "yandex_forms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    survey_id = Column(String, unique=True, index=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    owner = relationship("User", back_populates="forms")
    # При удалении формы удаляем связанные маршруты на уровне БД
    routes = relationship("Route", back_populates="form", cascade="all, delete-orphan")

class VkBot(Base):
    __tablename__ = "vk_bots"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    group_id = Column(String, nullable=False)
    encrypted_token = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    owner = relationship("User", back_populates="bots")
    # При удалении бота удаляем связанные маршруты на уровне БД
    routes = relationship("Route", back_populates="bot", cascade="all, delete-orphan")

class Route(Base):
    __tablename__ = "routes"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("yandex_forms.id", ondelete="CASCADE"), nullable=False)
    bot_id = Column(Integer, ForeignKey("vk_bots.id", ondelete="CASCADE"), nullable=False)
    target_vk_id = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    owner = relationship("User", back_populates="routes")
    form = relationship("YandexForm", back_populates="routes")
    bot = relationship("VkBot", back_populates="routes")