from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    
    forms = relationship("YandexForm", back_populates="owner")
    bots = relationship("VkBot", back_populates="owner")
    routes = relationship("Route", back_populates="owner")

class YandexForm(Base):
    __tablename__ = "yandex_forms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    survey_id = Column(String, unique=True, index=True, nullable=False) # ID формы в Яндексе
    owner_id = Column(Integer, ForeignKey("users.id"))
    
    owner = relationship("User", back_populates="forms")
    routes = relationship("Route", back_populates="form")

class VkBot(Base):
    __tablename__ = "vk_bots"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    group_id = Column(String, nullable=False)
    # Токен храним в зашифрованном виде!
    encrypted_token = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"))
    
    owner = relationship("User", back_populates="bots")
    routes = relationship("Route", back_populates="bot")

class Route(Base):
    __tablename__ = "routes"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("yandex_forms.id"))
    bot_id = Column(Integer, ForeignKey("vk_bots.id"))
    target_vk_id = Column(String, nullable=False) # Кому слать (VK ID)
    is_active = Column(Boolean, default=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    
    owner = relationship("User", back_populates="routes")
    form = relationship("YandexForm", back_populates="routes")
    bot = relationship("VkBot", back_populates="routes")