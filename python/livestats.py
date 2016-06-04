import sys
import MySQLdb
import MySQLdb.cursors
import nflgame
import argparse
import stat_functions as f
import math
import time
import datetime
import subprocess
import os

def update_games(year, week, weektype, update_all = False):
  global sql_now

  # ----------------------
  # update_games has two parts:
  # 1. update nfl_statistic for games currently in progress
  # 2. calculate fantasy_statistic based on data in nfl_statistic table
  #
  # Theoretically, you should never have to update nfl_statistic data after games are complete.
  # If you change any league scoring values, you may have to re-calculate the fantasy_statistic
  # values though.
  # ----------------------

  # Sometimes the url to the raw data is useful:
  # game_url = 'http://www.nfl.com/liveupdate/game-center/%s/%s_gtd.json'



  print "Udpating NFL Statistics (%s)" % (sql_now)
  livegamecount = update_nfl_statistics(year, week, weektype, update_all)

  if livegamecount == 0:
    print "No games currently in progress."

  print("--- %s seconds ---\n" % str(time.time()-start_time))


  print "Calculating and storing fantasy values"
  update_fantasy_statistics(year, week, weektype)

  print("--- %s seconds ---\n" % str(time.time()-start_time))

  # This is to finalize scores, but I think there might be a better way
  # update_schedule(year, week, weektype)

  print("--- %s seconds ---\n" % str(time.time()-start_time))

def update_nfl_statistics(year, week, weektype, update_all):
  global sql_now
  last_updated = sql_now

  # -----------------------------------------------------------------------------------------
  # 1. Get nfl_statistics from nflgame
  #  - Get nfl_schedule for selected week
  #  - For each game, if currently in progress:
  #    - Delete nfl_statistics with this game's gsis/gamekey
  #    - Loop through all game.player objects and insert 'direct mapped' stats
  #    - Insert team stats using game.stats_home and game.stats_away
  #    - loop through game.drives.plays() to calculate stats (this is the most complicated part)
  #
  # -----------------------------------------------------------------------------------------
  livegamecount = 0
  if int(year) > 2008: # years prior to 2008 need to be manually imported, they aren't in nflgame

    print '\nReading NFL stats for Week '+ str(week)

    games = nflgame.games(int(year), week=int(week), kind=weektype)

    print "Got all games"
    print("--- %s seconds ---\n" % str(time.time()-start_time))

    # variables to prune non-udpated rows later
    updated_games = ""


    for game in games:
      # ------------------------------------------
      # Update nfl_live_game, nfl_live_player
      # ------------------------------------------
      if game.playing() or game.game_over():
        livestatus = {}
        livestatus['gamekey'] = game.gamekey
        livestatus['to_go'] = game.togo
        livestatus['time'] = game.time.clock
        livestatus['quarter'] = game.time.qtr
        livestatus['down'] = game.down
        lastplay = None;
#      if game.gamekey == "56300":
        for drive in game.drives:
          livestatus['off'] = drive.team
          if drive.team == game.home:
            livestatus['def'] = game.away
          else:
            livestatus['def'] = game.home
          if drive.field_start is not None:
            livestatus['yardline'] = get_yard_line(drive.field_start.add_yards(drive.total_yds+drive.penalty_yds))
          else:
            livestatus['yardline'] = 0

          #print str(drive.field_start)+" "+str(drive.total_yds)

          for play in drive.plays:
            livestatus['note'] = play.data['note']
            livestatus['details'] = play.data['desc']
            lastplay = play
        if game.playing():
            update_live_players(lastplay, game.gamekey)

        if game.playing():
            query = 'select id from nfl_live_game where nfl_schedule_gsis = %s' % (livestatus['gamekey'])
            cur.execute(query)
            ls = livestatus
            if cur.rowcount > 0:
              query = (('update nfl_live_game set down = %s, to_go = %s, quarter = "%s", off_nfl_team_id = '+
                '(select id from nfl_team where club_id = "%s"), def_nfl_team_id = '+
                '(select id from nfl_team where club_id = "%s"), yard_line = %s, time="%s", home_score = %s, away_score = %s, note = "%s", details = "%s", play_id = %s '+
                'where id = %s') % (ls['down'],ls['to_go'],ls['quarter'],ls['off'],ls['def'],str(ls['yardline']),ls['time'],str(game.score_home), str(game.score_away),ls['note'],MySQLdb.escape_string(ls['details']),str(lastplay.playid),str(cur.fetchone()['id'])))
            else:
              query = (('insert into nfl_live_game (nfl_schedule_gsis, down, to_go, quarter, off_nfl_team_id, def_nfl_team_id, yard_line, time, week, nfl_week_type_id, year, home_score, away_score, note, details, play_id) values ('+
                '%s,%s,%s,"%s",(select id from nfl_team where club_id = "%s"),(select id from nfl_team where club_id = "%s"),%s,"%s",%s,(select id from nfl_week_type where text_id = "%s"),%s,%s,%s,"%s","%s",%s)') %
                (ls['gamekey'],ls['down'],ls['to_go'],ls['quarter'],ls['off'],ls['def'],str(ls['yardline']),ls['time'],str(week),weektype,str(year), str(game.score_home), str(game.score_away),ls['note'],MySQLdb.escape_string(ls['details']),str(lastplay.playid)))

            cur.execute(query)

        #print "nfl_live_game status updated for "+str(game)+" - "+str(game.time.qtr)

      # This updates nfl_statistic table, by default, just for live games update_all forces all games to be updated
      if game != None and (game.playing() or update_all):
        print str(game)+" - Q: "+str(game.time.qtr)
        livegamecount += 1
        # ----------------------------------------------
        # One Game - Create playerdict of stats
        # ----------------------------------------------

        # Playerdict to hold player_ids and scoring_cats/values for one game
        # playerdict = {}
        playerdict = dict(init_playerdict(str(game.home)).items() + init_playerdict(str(game.away)).items())

        # --------------------------
        # ADD STATS: simple 1:1 copy
        # --------------------------
        for player in game.players:
          add_other_player_stats(playerdict, player)
          for stat in player.stats:
            if playerdict.get(player.playerid) is None:
              playerdict[player.playerid] = {}
            playerdict[player.playerid][stat] = math.ceil(player.stats[stat])

        # --------------------------
        # ADD STATS: team stats
        # --------------------------
        home = game.home
        away = game.away
        # Team offensive line - Home
        playerdict[home+"_OL"]["team_rushing_yds"] = game.stats_home.rushing_yds
        playerdict[home+"_OL"]["team_passing_yds"] = game.stats_home.passing_yds
        # Team offensive line - Away
        playerdict[away+"_OL"]["team_rushing_yds"] = game.stats_away.rushing_yds
        playerdict[away+"_OL"]["team_passing_yds"] = game.stats_away.passing_yds
        # Team defense - Home
        playerdict[home+"_D"]["opp_score"] = game.score_away
        playerdict[home+"_D"]["opp_total_yds"] = game.stats_away.total_yds
        # Team defense - Away
        playerdict[away+"_D"]["opp_score"] = game.score_home
        playerdict[away+"_D"]["opp_total_yds"] = game.stats_home.total_yds
        # Def/ST Home
        playerdict[away+"_DST"]["opp_score"] = game.score_home
        playerdict[away+"_DST"]["opp_total_yds"] = game.stats_home.total_yds
        # Def/ST Away
        playerdict[home+"_DST"]["opp_score"] = game.score_away
        playerdict[home+"_DST"]["opp_total_yds"] = game.stats_away.total_yds
        # Special teams - Home
        # Special teams - Away

        # ---------------------------
        # ADD STATS: custom stats calculated by cycling through plays
        # ---------------------------


              #print str(play.data)+"\n"+game.time.qtr




            #print "game.gamekey: "+str(game.gamekey)
            #print "game.togo: "+str(game.togo)
            #print "game.time: "+str(game.time)
            #print "game.home: "+str(game.home)
            #print "game.away: "+str(game.away)
            #print "play.playid: "+str(play.playid)
            #print "play.yards_togo: "+str(play.yards_togo)
            #print "play.desc: "+str(play.desc)
            #print "play.down: "+str(play.down)
            #print "play.team: "+str(play.team)
            #print "playu.home: "+str(play.home)
            #print "\n"
        for play in game.drives.plays():
          for event in play.events:
            for stat in event:
              value = None
              stat_name = None
              # Stats for team players
              if stat == "passing_sk": # Start here
                f.team_sack(event, game, playerdict)
              if stat == "fumbles_lost":
                f.team_fumble(event, game, playerdict)
              if stat == "defense_int":
                f.team_defint(event, game, playerdict)
              if stat == "defense_tds":
                f.team_def_td(event, game, playerdict)
              if stat == "defense_safe":
                f.team_def_saf(event, game, playerdict)
              if stat == "puntret_tds" or stat == "kickret_tds":
                f.team_st_td(event, game, playerdict)
              # scenario where def recovers fumble, fumbles again and gets a TD
              if stat == "fumbles_rec_tds" and event["team"] != play.team:
                f.team_def_td(event, game, playerdict)

              # Stats for human players
              if stat == "kicking_fgm_yds": # Need yardages for each field goal
                #if playerdict.get(event["playerid"]) is None:  # new player, initialize
                #  playerdict[event["playerid"] = {}
                f.player_field_goal(event, playerdict)

              if (stat == "kickret_yds" or stat == "puntret_yds") and play.note != "FUMBLE":
                f.AddPlayerStat(stat, event, playerdict)
              if (stat == "kicking_fgmissed"):
                f.AddPlayerStat(stat, event, playerdict)
              if (stat == "rushing_tds") or (stat == "receiving_tds"):
                f.AddPlayerTD(stat, event, playerdict)

              if (stat == "fumbles_rec_tds"):
                  f.AddPlayerStat(stat,event,playerdict)
              if (stat == "fumbles_rec_yds"):
                  f.AddPlayerStat(stat,event,playerdict)




        # -----------------------------------------
        # Save playerdict to nfl_statistic table
        # -----------------------------------------


        for player in playerdict:
          for stat in playerdict[player]:
            query = ('select id, value from nfl_statistic where player_nfl_id = "%s" and nfl_scoring_cat_id = (select id from nfl_scoring_cat where text_id = "%s") '+
              'and nfl_schedule_gsis = %s') % (player, stat, str(game.gamekey))
            cur.execute(query)

            if cur.rowcount > 0: # Row already exists, update
              row = cur.fetchone()
              rowid = row['id']
              value = row['value']
              if value == playerdict[player][stat]: # Value is unchanged, just update last_updated
                  query = ('update nfl_statistic set last_updated = "%s" where id = %s') % (last_updated,str(rowid))
              else: # Value changed, set last_change so I know to update that fantasy statistic
                  query = ('update nfl_statistic set value = %s, last_updated = "%s", last_changed = "%s" where id = %s') % (str(playerdict[player][stat]), last_updated, last_updated, str(rowid))
              cur.execute(query)
            else: # insert new row
              query = (('insert into nfl_statistic (player_nfl_id, player_id, nfl_scoring_cat_id, value, week, nfl_week_type_id, year, nfl_schedule_gsis, last_changed) values '+
                '("%s", (select id from player where player_id = "%s"), (select id from nfl_scoring_cat where text_id = "%s"), %s,%s,(select id from nfl_week_type where text_id = "%s"),%s,%s,"%s")') %
                (player,player,stat,playerdict[player][stat],week,weektype,year,str(game.gamekey),last_updated))

              cur.execute(query)


          # else delete live status ?

        updated_games += game.gamekey+","
        # commit all changes for this game

        db.commit()
    # delete any rows that were not updated, they must not exist anymore, probably temp errors in live scoring
    if livegamecount > 0:
      updated_games = updated_games[:-1]
      query = 'delete from nfl_statistic where week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s") and year = %s and nfl_schedule_gsis in (%s) and last_updated < "%s"' % (week,weektype,year,updated_games,last_updated)
      cur.execute(query)
      deleted = cur.rowcount
      db.commit()
      print "Purged %s nfl_statistic rows." % str(deleted)
      query = 'delete from nfl_live_player where gsis_id not in (%s)' % (updated_games)
      cur.execute(query)

      query = 'delete from nfl_live_game where nfl_schedule_gsis not in (%s)' % (updated_games)
      cur.execute(query)
    else:
      query = 'truncate nfl_live_player'
      cur.execute(query)
      query = 'truncate nfl_live_game'
      cur.execute(query)
    db.commit()
    return livegamecount
    # Done looking for week_type spot through here

def update_fantasy_statistics(year, week, weektype):

  # ---------------------------------------------------------------------------------------------------------
  # 2. (Re)Calculate all fantasy_statistic for this week using nfl_statistic and scoring_def
  #   -  Select all nfl_statistic rows for current week
  #
  #
  #
  # ---------------------------------------------------------------------------------------------------------

  # TO MAKE IT FASTER
  # 1. In update_nfl_statistic, set last_changed for any stat that actually changed
  # 2. Update ONLY the stats where nfl_statistic_id was last_changed this run.
  # 3. Delete any stats that don't have a corresponding nfl_statistic_id, they must have been mistakes that were corrected

  # Get the current timestamp from sql so we can delete fantasy_statistics that no longer exist at the end.
  global sql_now
  last_updated = sql_now





  # Get all leagues of current weektype
  query = ('select league.id from league join league_settings on league.id = league_settings.league_id where nfl_season = "%s"' % weektype)
  cur.execute(query)
  leagues = cur.fetchall()

  # Get nfl_statistic stats for this week to be used to calculate fantasy stats for all leagues
  query = ('select nfl_statistic.player_id, nfl_statistic.id, nfl_scoring_cat_id, value, nfl_position.id as pos_id from nfl_statistic '+
	'inner join player on nfl_statistic.player_id = player.id inner join nfl_position on nfl_position.id = player.nfl_position_id '+
	'where year = %s and week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s")' % (str(year), str(week), weektype))


  query = ('select nfl_statistic.player_id, nfl_statistic.id, nfl_scoring_cat_id, value, nfl_position.id as pos_id from nfl_statistic '+
	'inner join player on nfl_statistic.player_id = player.id inner join nfl_position on nfl_position.id = player.nfl_position_id '+
	'where year = %s and week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s" and last_changed = "%s")' % (str(year), str(week), weektype, last_updated))

  if (args.recalc_all):
      query = ('select nfl_statistic.player_id, nfl_statistic.id, nfl_scoring_cat_id, value, nfl_position.id as pos_id from nfl_statistic '+
    	'inner join player on nfl_statistic.player_id = player.id inner join nfl_position on nfl_position.id = player.nfl_position_id '+
    	'where year = %s and week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s")' % (str(year), str(week), weektype))

  cur.execute(query)
  print
  print '******'
  print '******   %s fantasy_statistics to calculate' % (str(cur.rowcount))
  print '******'
  print
  nfl_stat_rows = cur.fetchall()
  # for each league, get_scoring_def_dict
  for l in leagues:
      leagueid = l['id']
      scoring_def = get_scoring_def_dict(leagueid)
      for row in nfl_stat_rows:  # for each nfl_statistic this week, find a scoring_def for this league
        s = None
        if scoring_def.get(row['pos_id']) is not None and scoring_def[row['pos_id']].get(row['nfl_scoring_cat_id']) is not None:
          s = scoring_def[row['pos_id']][row['nfl_scoring_cat_id']]
        elif scoring_def.get(0) is not None and scoring_def[0].get(row['nfl_scoring_cat_id']) is not None:
          s = scoring_def[0][row['nfl_scoring_cat_id']]


        if s is not None:
          if s['round'] == 1:
            points = int(math.ceil(row['value'] * (s['points']/float(s['per']))))
          else:
            points = int(math.floor(row['value'] * (s['points']/float(s['per']))))

          # query = (('select id from fantasy_statistic where nfl_statistic_id = %s') % (row['id']))
          query = (('select id, points from fantasy_statistic where week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s") and year = %s and player_id = %s and league_id = %s and nfl_scoring_cat_id = %s')
            % (str(week),weektype,str(year),str(row['player_id']),leagueid,row['nfl_scoring_cat_id']))

          #query = (('select id from fantasy_statistic where nfl_statistic_id = %s') % (row['id']))
          cur.execute(query)
          if cur.rowcount > 0:
            query = 'update fantasy_statistic set points = %s, last_updated = now(), nfl_statistic_id = %s where id = %s' % (str(points),str(row['id']),str(cur.fetchone()['id']))
          else:
            query = 'insert into fantasy_statistic (player_id, nfl_scoring_cat_id, points, week, nfl_week_type_id, year, league_id, nfl_statistic_id) values (%s,%s,%s,%s,(select id from nfl_week_type where text_id = "%s"),%s,%s,%s)' % (row['player_id'],row['nfl_scoring_cat_id'],str(points),str(week),weektype,str(year),leagueid,row['id'])
          cur.execute(query)

      # Really should confirm changes where made somehow, before confirming the live_scores_key should be updated
      query = 'update league_settings set live_scores_key = UNIX_TIMESTAMP() where league_settings.league_id = %s' % (str(leagueid))

      cur.execute(query)
      db.commit()

  # Delete any stats that were no longer found in this update, they must have been retracted ?
  # all fantasy_statistics are recaculated for the given week, even if not all games are live
  #query = 'delete from fantasy_statistic where year = %s and week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s") and last_updated < "%s"' % (str(year), str(week), weektype, last_updated)
  #query = 'select * from fantasy_statistic where year = %s and week = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s") and last_updated < "%s"' % (str(year), str(week), weektype, last_updated)
  query = (('delete from fantasy_statistic where week = %s and year = %s and nfl_week_type_id = (select id from nfl_week_type where text_id = "%s") and '+
            'nfl_statistic_id not in (select id from nfl_statistic where week = %s and year = %s and '+
            'nfl_week_type_id = (select id from nfl_week_type where text_id = "%s"))') % (str(week),str(year),weektype,str(week),str(year),weektype))

  cur.execute(query)

  deleted = cur.rowcount
  db.commit()

  print "Purged %s fantasy_statistic rows." % (str(deleted))

def get_yard_line(yard_line, team = ""):

  if str(yard_line) == '50' or str(yard_line) == 'MIDFIELD':
    return 0

  territory, yd_str = str(yard_line).split()
  yd = int(yd_str)
  if team == "":
    if territory == 'OWN':
      return -(50 - yd)
    else:
      return 50 - yd
  else:
    if territory == team:
      return -(50 - yd)
    else:
      return 50 - yd

def get_scoring_def_dict(leagueid):
  query = (('select scoring_def.nfl_scoring_cat_id, per, points, round, nfl_position_id from scoring_def '+
  'join nfl_scoring_cat on nfl_scoring_cat.id = scoring_def.nfl_scoring_cat_id where league_id = %s') % str(leagueid))

  cur.execute(query)

  scoring_def = {}
  for row in cur.fetchall():
    if scoring_def.get(row['nfl_position_id']) is None:
      scoring_def[row['nfl_position_id']] = {}
    scoring_def[row['nfl_position_id']][row['nfl_scoring_cat_id']] = {}
    scoring_def[row['nfl_position_id']][row['nfl_scoring_cat_id']]['per'] = row['per']
    scoring_def[row['nfl_position_id']][row['nfl_scoring_cat_id']]['round'] = row['round']
    scoring_def[row['nfl_position_id']][row['nfl_scoring_cat_id']]['points'] = row['points']
  return scoring_def

def add_other_player_stats(playerdict, player):
  # add rushing/receiving combined stat
  rush = rec = 0
  if player.stats.has_key('rushing_yds'):
    rush = player.stats['rushing_yds']
  if player.stats.has_key('receiving_yds'):
    rec = player.stats['receiving_yds']
  yards = rush + rec
  if player.stats.has_key('rushing_yds') or player.stats.has_key('receiving_yds'):
    if playerdict.get(player.playerid) is None:
      playerdict[player.playerid] = {}
    playerdict[player.playerid]['rush_rec_yds'] = yards

def update_live_players(play, gamekey):
    for event in play.events:
        elist = list()
        for key, value in event.iteritems():
            elist.append(key)
        text = ""

        if "rushing_tds" in elist and "rushing_yds" in elist and text == "":
            text = str(event["rushing_yds"])+" yard TD run!"

        if "passing_tds" in elist and "passing_yds" in elist and text == "":
            text = str(event["passing_yds"])+" yard TD pass!"

        if "receiving_tds" in elist and "receiving_yds" in elist and text == "":
            text = str(event["receiving_yds"])+" yard TD catch!"

        if "receiving_yds" in elist and text == "":
            text = str(event["receiving_yds"])+" yard catch"

        if "passing_yds" in elist and text == "":
            text = str(event["passing_yds"])+" yard completion"

        if "rushing_yds" in elist and text == "":
            text = str(event["rushing_yds"])+" yard run"

        if "passing_ints" in elist and text == "":
            text = "Interception!"

        if "kicking_fgm" in elist and text == "":
            text = str(event["kicking_fgm_yds"]) + " yard FG is good!"

        if "kicking_fgmissed" in elist and text == "":
            text = "Field goal missed!"

        if "kicking_xpmade" in elist and text == "":
            text = "XP is Good!"

        if "kicking_xpmade" in elist and text == "":
            text = "Extra point is good."

        if "kicking_xpmissed" in elist and text == "":
            text = "Extra point missed!"

        if "fumbles_lost" in elist and text == "":
            text = "FUMBLE LOST!"





        if text != "":
            query = 'select id from nfl_live_player where nfl_player_id = "%s"' % (event['playerid'])
            cur.execute(query)
            if cur.rowcount > 0:
                query = ('update nfl_live_player set play_id = %s, text="%s" where nfl_player_id = "%s"'
                        % (str(play.playid),text,event['playerid']))
            else:
                query = (('insert into nfl_live_player (player_id, gsis_id, play_id, text, nfl_player_id)'+
                    'values((select id from player where player_id = "%s"),%s,%s,"%s","%s")')
                    % (event['playerid'],str(gamekey),str(play.playid),text,event['playerid']))
            cur.execute(query)

			#print key
			#print value
    db.commit()