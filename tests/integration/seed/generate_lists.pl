#!/usr/bin/env perl
# Generate a throwaway "lore" upstream for the integration tests.
#
# Creates LIST_COUNT public-inbox v2 inboxes (list_0 .. list_N) under
# $SEED_ROOT, each with MESSAGES_PER_LIST synthetic messages and a
# refs/meta/origins blob, then writes the public-inbox config that
# public-inbox-netd serves.  grok-pull mirrors this exactly like
# lore.kernel.org: it reads /manifest.js.gz and clones
# /<inbox>/git/<epoch>.git over HTTP.
#
# Emails are synthesized here (no .eml fixture files) so the generated set is
# deterministic and can be scaled with LIST_COUNT.
#
# Tests/integration/seed/mutate.pl reuses seed_common.pm to add lists and
# messages to a running upstream.

use strict;
use warnings;
use v5.10.1;
use File::Basename qw(dirname);
use lib dirname(__FILE__);
use File::Path qw(make_path);
use File::Spec;

use seed_common qw(
	init_git_identity create_list write_upstream_config with_scratch
);

my $ROOT = $ENV{SEED_ROOT} // '/data';
my $PI_CONFIG = $ENV{PI_CONFIG} // "$ROOT/pi-config";
my $LIST_COUNT = $ENV{LIST_COUNT} // 360;
my $PER_LIST = $ENV{MESSAGES_PER_LIST} // 3;

die "LIST_COUNT must be a positive integer\n"
	unless $LIST_COUNT =~ /\A[0-9]+\z/ && $LIST_COUNT > 0;
die "MESSAGES_PER_LIST must be a positive integer\n"
	unless $PER_LIST =~ /\A[0-9]+\z/ && $PER_LIST > 0;

make_path($ROOT) unless -d $ROOT;
init_git_identity();

my $started = time;
printf "Generating %d inboxes (%d messages each) under %s\n",
	$LIST_COUNT, $PER_LIST, $ROOT;

my @names = map { "list_$_" } 0 .. $LIST_COUNT - 1;
with_scratch($ROOT, sub {
	for my $i (0 .. $LIST_COUNT - 1) {
		create_list(
			root => $ROOT,
			name => $names[$i],
			index => $i,
			messages => $PER_LIST,
		);
		printf "  [%d/%d] %s\n", $i + 1, $LIST_COUNT, $names[$i]
			if ($i + 1) % 25 == 0 || $i + 1 == $LIST_COUNT;
	}
});

write_upstream_config(path => $PI_CONFIG, root => $ROOT, names => \@names);

printf "Done: %d inboxes in %ds (config: %s)\n",
	scalar(@names), time - $started, $PI_CONFIG;
