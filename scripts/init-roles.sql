-- Executed once by the postgres image on first start.
create role genranq_owner login password 'genranq_owner';
create role genranq_app login password 'genranq_app' nobypassrls;
create database genranq owner genranq_owner;
\c genranq
create extension if not exists pgcrypto;
create extension if not exists citext;
