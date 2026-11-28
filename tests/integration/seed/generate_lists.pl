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

use strict;
use warnings;
use v5.10.1;
use File::Path qw(make_path remove_tree);
use File::Spec;
use Cwd qw(abs_path);
use IPC::Open2 qw(open2);

my $ROOT = $ENV{SEED_ROOT} // '/data';
my $PI_CONFIG = $ENV{PI_CONFIG} // "$ROOT/pi-config";
my $LIST_COUNT = $ENV{LIST_COUNT} // 360;
my $PER_LIST = $ENV{MESSAGES_PER_LIST} // 3;

die "LIST_COUNT must be a positive integer\n"
	unless $LIST_COUNT =~ /\A[0-9]+\z/ && $LIST_COUNT > 0;
die "MESSAGES_PER_LIST must be a positive integer\n"
	unless $PER_LIST =~ /\A[0-9]+\z/ && $PER_LIST > 0;

require PublicInbox::Eml;
require PublicInbox::TestCommon;

make_path($ROOT) unless -d $ROOT;

# Deterministic identity for the refs/meta/origins commits below.
for my $var (qw(AUTHOR COMMITTER)) {
	$ENV{"GIT_${var}_NAME"} = 'Integration Test';
	$ENV{"GIT_${var}_EMAIL"} = 'integration\@integration.test';
	$ENV{"GIT_${var}_DATE"} = '2021-01-01T00:00:00+0000';
}

# create_inbox() builds its repo under ./t/data-gen and then copies it to
# tmpdir, so run from a scratch directory that gets removed at the end.
my $work = File::Spec->catdir($ROOT, '.seed-work');
remove_tree($work) if -d $work;
make_path($work) or die "Cannot create $work: $!\n";
chdir $work or die "Cannot chdir $work: $!\n";

# A fixed Date keeps inbox fingerprints stable between runs.
my $DATE = 'Fri, 01 Jan 2021 00:00:00 +0000';

sub make_eml {
	my ($name, $n) = @_;
	my $raw = <<"EOM";
From: sender-$name-$n\@integration.test
To: $name\@integration.test
Cc: $name\@integration.test
Date: $DATE
Subject: $name message $n
Message-Id: <$name-$n\@integration.test>
List-Id: $name.integration.test
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

integration test body for $name message $n
EOM
	PublicInbox::Eml->new($raw);
}

sub origins_blob {
	my ($name, $i) = @_;
	return <<"EOM";
[publicinbox "$name"]
	address = $name\@integration.test
	description = Integration test list $i
	listid = $name.integration.test
EOM
}

# Run git with $input on stdin and return its stdout (dies on failure).
sub git_stdin {
	my ($git_dir, $input, @args) = @_;
	my ($rdr, $wtr);
	my $pid = open2($rdr, $wtr, 'git', "--git-dir=$git_dir", @args);
	print $wtr $input;
	close $wtr;
	local $/;
	my $out = <$rdr> // '';
	close $rdr;
	waitpid($pid, 0);
	die "git @args failed in $git_dir\n" if $? != 0;
	return $out;
}

# Write refs/meta/origins:i, the grokmirror/lore convention the controller
# reads inbox addresses from.  Done with plain git so it does not depend on
# the public-inbox version's Import API.
sub write_origins_ref {
	my ($dir, $name, $i) = @_;
	my $git_dir = File::Spec->catdir($dir, 'git', '0.git');
	my $blob = git_stdin($git_dir, origins_blob($name, $i),
		qw(hash-object -w --stdin));
	chomp $blob;
	my $tree = git_stdin($git_dir, "100644 blob $blob\ti\n", 'mktree');
	chomp $tree;
	my $commit = git_stdin($git_dir, "origins for $name\n",
		'commit-tree', $tree);
	chomp $commit;
	git_stdin($git_dir, '', 'update-ref', 'refs/meta/origins', $commit);
}

sub create_list {
	my ($i) = @_;
	my $name = "list_$i";
	my $dir = File::Spec->catdir($ROOT, $name);

	PublicInbox::TestCommon::create_inbox(
		$name,
		version => 2,
		indexlevel => 'basic',
		tmpdir => $dir,
		-no_gc => 1,
			sub {
			my ($writable, $ibx) = @_;
			for my $n (0 .. $PER_LIST - 1) {
				$writable->add(make_eml($name, $n))
					or die "add failed for $name message $n: $@\n";
			}
		},
		);

	# The controller reads addresses from refs/meta/origins:i (like lore
	# mirrors do) instead of hitting the upstream config endpoint for every
	# inbox (which is rate limited).  Written after create_inbox() so the
	# importer never rewrites it.
	write_origins_ref($dir, $name, $i);

	# public-inbox detects the v2 format by the presence of inbox.lock; the
	# importer does not create it, public-inbox-init does.
	open(my $lock, '>>', File::Spec->catfile($dir, 'inbox.lock'))
		or die "Cannot create inbox.lock for $name: $!\n";
	close($lock);

	# create_inbox() leaves its build markers and journals behind; they are
	# not part of a real mirror and would confuse later reruns.  inbox.lock
	# and open.lock are part of the v2 layout and must stay.
	for my $f (qw(creat.lock creat.stamp msgmap.sqlite3-journal)) {
		unlink File::Spec->catfile($dir, $f);
	}
	return $dir;
}

my $started = time;
printf "Generating %d inboxes (%d messages each) under %s\n",
	$LIST_COUNT, $PER_LIST, $ROOT;

my @dirs;
for my $i (0 .. $LIST_COUNT - 1) {
	push @dirs, create_list($i);
	printf "  [%d/%d] %s\n", $i + 1, $LIST_COUNT, $dirs[-1]
		if ($i + 1) % 25 == 0 || $i + 1 == $LIST_COUNT;
}

# Write the upstream public-inbox config: grokmanifest=all makes
# /manifest.js.gz list every inbox, wwwListing=all does the same for the HTML
# index.
my $tmp = "$PI_CONFIG.tmp";
open(my $fh, '>', $tmp) or die "Cannot write $tmp: $!\n";
print $fh <<'EOM';
; Generated by tests/integration/seed/generate_lists.pl
[publicinbox]
	grokmanifest = all
	wwwListing = all

EOM
for my $i (0 .. $LIST_COUNT - 1) {
	my $name = "list_$i";
	my $dir = File::Spec->catdir($ROOT, $name);
	print $fh <<"EOM";
[publicinbox "$name"]
	inboxdir = $dir
	url = /$name
	address = $name\@integration.test
	description = Integration test list $i
	listid = $name.integration.test
	indexlevel = basic

EOM
}
close($fh) or die "Cannot close $tmp: $!\n";
rename($tmp, $PI_CONFIG) or die "Cannot rename $tmp to $PI_CONFIG: $!\n";

chdir $ROOT or die "Cannot chdir $ROOT: $!\n";
remove_tree($work);
printf "Done: %d inboxes in %ds (config: %s)\n",
	scalar(@dirs), time - $started, $PI_CONFIG;
