\set ON_ERROR_STOP on

DROP TABLE IF EXISTS public.__domeye_feature_country_refresh;
CREATE UNLOGGED TABLE public.__domeye_feature_country_refresh
(LIKE public.feature_country INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
