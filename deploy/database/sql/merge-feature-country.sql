\set ON_ERROR_STOP on

INSERT INTO public.feature_country (
    t,
    source,
    country,
    v4prefix_num,
    v6prefix_num,
    v4ip_num,
    announ_num,
    withdraw_num
)
SELECT
    t,
    source,
    country,
    v4prefix_num,
    v6prefix_num,
    v4ip_num,
    announ_num,
    withdraw_num
FROM public.__domeye_feature_country_refresh
ON CONFLICT (t, source, country) DO UPDATE SET
    v4prefix_num = EXCLUDED.v4prefix_num,
    v6prefix_num = EXCLUDED.v6prefix_num,
    v4ip_num = EXCLUDED.v4ip_num,
    announ_num = EXCLUDED.announ_num,
    withdraw_num = EXCLUDED.withdraw_num;

DROP TABLE public.__domeye_feature_country_refresh;
