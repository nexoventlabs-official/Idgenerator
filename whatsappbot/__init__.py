"""
WhatsApp Bot Module for Voter ID Card Generator
================================================
Uses Meta WhatsApp Business Cloud API to replicate
the website chatbot flow on WhatsApp.
"""

from .routes import whatsapp_bp

__all__ = ['whatsapp_bp']
