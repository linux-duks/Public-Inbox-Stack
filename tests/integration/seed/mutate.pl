#!/usr/bin/env perl
# Mutate a running integration-test upstream.
#
# Adds new messages to the existing lists and/or creates new lists, so the
# test can verify that a second grok-pull + index cycle picks both up.  Run it
# inside the upstream container (it needs the generated inboxes and PI_CONFIG):
#
#   perl /seed/mutate.pl --baseline-count 40 --add-lists 10 --add-emails 2
#
#   --baseline-count N   lists that already exist (list_0..list_<N-1>)
#   --add-lists M        create list_<N>..list_<N+M-1> and advertise them
#   --add-emails K       append K "list_i new j" messages to each existing list
#
# The messages are added first, then the new lists, then PI_CONFIG is
# extended and the daemon reloaded, so the upstream is never left in a state
# where the config advertises an inbox that does not exist yet.

use strict;
use warnings;
use v5.10.1;
use File::Basename qw(dirname);
use lib dirname(__FILE__);
use Getopt::Long qw(GetOptions);
use File::Spec;

use seed_common qw(
	init_git_identity make_eml create_list append_inbox_config
	with_scratch reload_netd
);

my $ROOT = $ENV{SEED_ROOT} // '/data';
my $PI_CONFIG = $ENV{PI_CONFIG} // "$ROOT/pi-config";

my $baseline = 0;
my $add_lists = 0;
my $add_emails = 0;
GetOptions(
	'baseline-count=i' => \$baseline,
	'add-lists=i' => \$add_lists,
	'add-emails=i' => \$add_emails,
) or die "usage: $0 --baseline-count N [--add-lists M] [--add-emails K]\n";

die "--baseline-count must be a positive integer\n" unless $baseline > 0;
die "--add-lists must not be negative\n" if $add_lists < 0;
die "--add-emails must not be negative\n" if $add_emails < 0;
die "nothing to do (--add-lists and --add-emails are both 0)\n"
	unless $add_lists > 0 || $add_emails > 0;
die "$PI_CONFIG not found; is the upstream seeded?\n" unless -f $PI_CONFIG;

init_git_identity();

require PublicInbox::Config;
require PublicInbox::InboxWritable;
my $cfg = PublicInbox::Config->new($PI_CONFIG);

sub inbox_writable {
	my ($name) = @_;
	my $ibx = $cfg->lookup_name($name)
		or die "inbox '$name' is not in $PI_CONFIG\n";
	return ($ibx, PublicInbox::InboxWritable->new($ibx, { nproc => 1 }));
}

# 1. Append new messages to every existing list.
for my $i (0 .. $baseline - 1) {
	my $name = "list_$i";
	my ($ibx, $writable) = inbox_writable($name);
	my $before = $ibx->mm->max // 0;
	my $im = $writable->importer(0);
	my $added = 0;
	for my $n (0 .. $add_emails - 1) {
		# ->add() returns undef for a message that is already stored, so a
		# repeated mutation is a no-op instead of a hard failure.
		$added++ if $im->add(make_eml(name => $name, index => $n, kind => 'new'));
	}
	$im->done;
	delete $ibx->{mm}; # the importer updated msgmap.sqlite3 on disk
	my $after = $ibx->mm->max // 0;
	printf "  %s: %d -> %d messages (%d added)\n", $name, $before, $after, $added;
}

# 2. Create the new lists.
my @new = map { "list_$_" } $baseline .. $baseline + $add_lists - 1;
with_scratch($ROOT, sub {
	my $offset = 0;
	for my $name (@new) {
		create_list(
			root => $ROOT,
			name => $name,
			index => $baseline + $offset,
			messages => 3,
		);
		printf "  %s: created\n", $name;
		$offset++;
	}
});

# 3. Advertise them and reload the daemon so they are served immediately.
if (@new) {
	append_inbox_config(
		path => $PI_CONFIG,
		root => $ROOT,
		entries => [
			map { [ $new[$_], $baseline + $_ ] } 0 .. $#new
		],
	);
	printf "Advertised %d new list(s) in %s\n", scalar(@new), $PI_CONFIG;
	reload_netd();
}

printf "Mutation done: %d new list(s), %d new message(s) per existing list\n",
	scalar(@new), $add_emails;
