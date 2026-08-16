\set ON_ERROR_STOP on

SET domeye.refresh_table TO :'table_name';
SET domeye.refresh_family TO :'table_family';
SET domeye.refresh_is_feature TO :'is_feature';

DO $block$
DECLARE
    target_table text := current_setting('domeye.refresh_table');
    table_family text := current_setting('domeye.refresh_family');
    is_feature boolean := current_setting('domeye.refresh_is_feature')::boolean;
    template_table text;
    temporary_template text := '__domeye_refresh_template';
BEGIN
    IF target_table !~ '^[a-z_]+_[0-9]{6}$'
       OR table_family !~ '^[a-z_]+$' THEN
        RAISE EXCEPTION '刷新表名不符合白名单格式：%', target_table;
    END IF;

    EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', temporary_template);

    IF to_regclass(format('public.%I', target_table)) IS NOT NULL THEN
        EXECUTE format(
            'CREATE TABLE public.%I (LIKE public.%I INCLUDING ALL)',
            temporary_template,
            target_table
        );
        template_table := temporary_template;
    ELSE
        SELECT tablename
        INTO template_table
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename ~ ('^' || table_family || '_[0-9]{6}$')
        ORDER BY right(tablename, 6) DESC
        LIMIT 1;
    END IF;

    IF template_table IS NULL THEN
        RAISE EXCEPTION '刷新表 % 缺少同族模板', target_table;
    END IF;

    EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', target_table);
    EXECUTE format(
        'CREATE TABLE public.%I (LIKE public.%I INCLUDING ALL)',
        target_table,
        template_table
    );

    IF temporary_template = template_table THEN
        EXECUTE format('DROP TABLE public.%I', temporary_template);
    END IF;

    IF is_feature THEN
        PERFORM create_hypertable(
            format('public.%I', target_table)::regclass,
            't',
            if_not_exists => true
        );
    END IF;
END
$block$;
