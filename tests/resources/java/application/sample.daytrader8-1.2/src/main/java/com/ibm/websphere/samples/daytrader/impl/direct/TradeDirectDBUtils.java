/**
 * (C) Copyright IBM Corporation 2019.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.impl.direct;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.math.BigDecimal;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;

import javax.annotation.Resource;
import javax.enterprise.context.ApplicationScoped;
import javax.inject.Inject;
import javax.sql.DataSource;

import com.ibm.websphere.samples.daytrader.beans.RunStatsDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.TradeDB;
import com.ibm.websphere.samples.daytrader.interfaces.TradeJDBC;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.MDBStats;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

/**
 * TradeBuildDB uses operations provided by the TradeApplication to (a) create the Database tables
 * (b)populate a DayTrader database without creating the tables. Specifically, a
 * new DayTrader User population is created using UserIDs of the form "uid:xxx"
 * where xxx is a sequential number (e.g. uid:0, uid:1, etc.). New stocks are also created of the
 * form "s:xxx", again where xxx represents sequential numbers (e.g. s:1, s:2, etc.)
 */
@ApplicationScoped
public class TradeDirectDBUtils implements TradeDB {

  // For Wildfly - add java:/ to this resource.
  
  @Resource(lookup = "jdbc/TradeDataSource")
  private DataSource datasource;

  @Inject 
  @TradeJDBC
  TradeServices ts;

  public String checkDBProductName() throws Exception {
    Connection conn = null;
    String dbProductName = null;

    try {

      conn = datasource.getConnection();
      DatabaseMetaData dbmd = conn.getMetaData();
      dbProductName = dbmd.getDatabaseProductName();
    } catch (SQLException e) {
      Log.error(e, "TradeDirect:checkDBProductName() -- Error checking the Daytrader Database Product Name");
    } finally {
      conn.close();
    }
    return dbProductName;
  }


  /**
   * Re-create the DayTrader db tables and populate them OR just populate a DayTrader DB, logging to the provided output stream
   */
  public void buildDB(java.io.PrintWriter out, InputStream ddlFile) throws Exception {
    String symbol, companyName;
    int errorCount = 0; // Give up gracefully after 10 errors

    //  TradeStatistics.statisticsEnabled=false;  // disable statistics
    out.println("<HEAD><BR><EM> TradeBuildDB: Building DayTrader Database...</EM><BR> This operation will take several minutes. Please wait...</HEAD>");
    out.println("<BODY>");

    if (ddlFile != null) {
      //out.println("<BR>TradeBuildDB: **** warPath= "+warPath+" ****</BR></BODY>");

      boolean success = false;

      Object[] sqlBuffer = null;

      //parse the DDL file and fill the SQL commands into a buffer
      try {
        sqlBuffer = parseDDLToBuffer(ddlFile);
      } catch (Exception e) {
        Log.error(e, "TradeBuildDB: Unable to parse DDL file");
        out.println("<BR>TradeBuildDB: **** Unable to parse DDL file for the specified database ****</BR></BODY>");
        return;
      }
      if ((sqlBuffer == null) || (sqlBuffer.length == 0)) {
        out.println("<BR>TradeBuildDB: **** Parsing DDL file returned empty buffer, please check that a valid DB specific DDL file is available and retry ****</BR></BODY>");
        return;
      }

      // send the sql commands buffer to drop and recreate the Daytrader tables
      out.println("<BR>TradeBuildDB: **** Dropping and Recreating the DayTrader tables... ****</BR>");
      try {
        success = recreateDBTables(sqlBuffer, out);
      } catch (Exception e) {
        Log.error(e, "TradeBuildDB: Unable to drop and recreate DayTrader Db Tables, please check for database consistency before continuing");
        out.println("TradeBuildDB: Unable to drop and recreate DayTrader Db Tables, please check for database consistency before continuing");
        return;
      }
      if (!success) {
        out.println("<BR>TradeBuildDB: **** Unable to drop and recreate DayTrader Db Tables, please check for database consistency before continuing ****</BR></BODY>");
        return;
      }
      out.println("<BR>TradeBuildDB: **** DayTrader tables successfully created! ****</BR><BR><b> Please Stop and Re-start your Daytrader application (or your application server) and then use the \"Repopulate Daytrader Database\" link to populate your database.</b></BR><BR><BR></BODY>");
      return;
    } // end of createDBTables

    out.println("<BR>TradeBuildDB: **** Creating " + TradeConfig.getMAX_QUOTES() + " Quotes ****</BR>");
    //Attempt to delete all of the Trade users and Trade Quotes first
    try {
      resetTrade(true);
    } catch (Exception e) {
      Log.error(e, "TradeBuildDB: Unable to delete Trade users (uid:0, uid:1, ...) and Trade Quotes (s:0, s:1, ...)");
    }
    for (int i = 0; i < TradeConfig.getMAX_QUOTES(); i++) {
      symbol = "s:" + i;
      companyName = "S" + i + " Incorporated";
      try {
        ts.createQuote(symbol, companyName, new java.math.BigDecimal(TradeConfig.rndPrice()));
        if (i % 10 == 0) {
          out.print("....." + symbol);
          if (i % 100 == 0) {
            out.println(" -<BR>");
            out.flush();
          }
        }
      } catch (Exception e) {
        if (errorCount++ >= 10) {
          String error = "Populate Trade DB aborting after 10 create quote errors. Check the EJB datasource configuration. Check the log for details <BR><BR> Exception is: <BR> "
              + e.toString();
          Log.error(e, error);
          throw e;
        }
      }
    }
    out.println("<BR>");
    out.println("<BR>**** Registering " + TradeConfig.getMAX_USERS() + " Users **** ");
    errorCount = 0; //reset for user registrations

    // Registration is a formal operation in Trade 2.
    for (int i = 0; i < TradeConfig.getMAX_USERS(); i++) {
      String userID = "uid:" + i;
      String fullname = TradeConfig.rndFullName();
      String email = TradeConfig.rndEmail(userID);
      String address = TradeConfig.rndAddress();
      String creditcard = TradeConfig.rndCreditCard();
      double initialBalance = (double) (TradeConfig.rndInt(100000)) + 200000;
      if (i == 0) {
        initialBalance = 1000000; // uid:0 starts with a cool million.
      }
      try {
        AccountDataBean accountData = ts.register(userID, "xxx", fullname, address, email, creditcard, new BigDecimal(initialBalance));

        if (accountData != null) {
          if (i % 50 == 0) {
            out.print("<BR>Account# " + accountData.getAccountID() + " userID=" + userID);
          } // end-if

          int holdings = TradeConfig.rndInt(TradeConfig.getMAX_HOLDINGS() + 1); // 0-MAX_HOLDING (inclusive), avg holdings per user = (MAX-0)/2
          double quantity = 0;

          for (int j = 0; j < holdings; j++) {
            symbol = TradeConfig.rndSymbol();
            quantity = TradeConfig.rndQuantity();
            ts.buy(userID, symbol, quantity, TradeConfig.getOrderProcessingMode());
          } // end-for
          if (i % 50 == 0) {
            out.println(" has " + holdings + " holdings.");
            out.flush();
          } // end-if
        } else {
          out.println("<BR>UID " + userID + " already registered.</BR>");
          out.flush();
        } // end-if

      } catch (Exception e) {
        if (errorCount++ >= 10) {
          String error = "Populate Trade DB aborting after 10 user registration errors. Check the log for details. <BR><BR> Exception is: <BR>"
              + e.toString();
          Log.error(e, error);
          throw e;
        }
      }
    } // end-for
    out.println("</BODY>");
  }

  private boolean recreateDBTables(Object[] sqlBuffer, java.io.PrintWriter out) throws Exception {
    // Clear MDB Statistics
    MDBStats.getInstance().reset();

    Connection conn = null;
    boolean success = false;
    try {
      conn = datasource.getConnection();
      Statement stmt = conn.createStatement();
      int bufferLength = sqlBuffer.length;
      for (int i = 0; i < bufferLength; i++) {
        try {
          stmt.executeUpdate((String) sqlBuffer[i]);
          // commit(conn);
        } catch (SQLException ex) {
          // Ignore DROP statements as tables won't always exist.
          if (((String) sqlBuffer[i]).indexOf("DROP ") < 0) {
            Log.error("TradeDirect:recreateDBTables SQL Exception thrown on executing the foll sql command: " + sqlBuffer[i], ex);
            out.println("<BR>SQL Exception thrown on executing the foll sql command: <I>" + sqlBuffer[i] + "</I> . Check log for details.</BR>");
          }
        }
      }
      stmt.close();
      conn.commit();
      success = true;
    } catch (Exception e) {
      Log.error(e, "TradeDirect:recreateDBTables() -- Error dropping and recreating the database tables");
    } finally {
      conn.close();
    }
    return success;
  }


  public RunStatsDataBean resetTrade(boolean deleteAll) throws Exception {
    // Clear MDB Statistics
    MDBStats.getInstance().reset();
    // Reset Trade

    RunStatsDataBean runStatsData = new RunStatsDataBean();
    Connection conn = null;
    try {

      conn = datasource.getConnection();
      PreparedStatement stmt = null;
      ResultSet rs = null;

      if (deleteAll) {
        try {
          stmt = getStatement(conn, "delete from quoteejb");
          stmt.executeUpdate();
          stmt.close();
          stmt = getStatement(conn, "delete from accountejb");
          stmt.executeUpdate();
          stmt.close();
          stmt = getStatement(conn, "delete from accountprofileejb");
          stmt.executeUpdate();
          stmt.close();
          stmt = getStatement(conn, "delete from holdingejb");
          stmt.executeUpdate();
          stmt.close();
          stmt = getStatement(conn, "delete from orderejb");
          stmt.executeUpdate();
          stmt.close();
          // FUTURE: - DuplicateKeyException - For now, don't start at
          // zero as KeySequenceDirect and KeySequenceBean will still
          // give out
          // the cached Block and then notice this change. Better
          // solution is
          // to signal both classes to drop their cached blocks
          // stmt = getStatement(conn, "delete from keygenejb");
          // stmt.executeUpdate();
          // stmt.close();
          conn.commit();
        } catch (Exception e) {
          Log.error(e, "TradeDirect:resetTrade(deleteAll) -- Error deleting Trade users and stock from the Trade database");
        }
        return runStatsData;
      }

      stmt = getStatement(conn, "delete from holdingejb where holdingejb.account_accountid is null");
      stmt.executeUpdate();
      stmt.close();

      // Count and Delete newly registered users (users w/ id that start
      // "ru:%":
      stmt = getStatement(conn, "delete from accountprofileejb where userid like 'ru:%'");
      stmt.executeUpdate();
      stmt.close();

      stmt = getStatement(conn, "delete from orderejb where account_accountid in (select accountid from accountejb a where a.profile_userid like 'ru:%')");
      stmt.executeUpdate();
      stmt.close();

      stmt = getStatement(conn,
          "delete from holdingejb where account_accountid in (select accountid from accountejb a where a.profile_userid like 'ru:%')");
      stmt.executeUpdate();
      stmt.close();

      stmt = getStatement(conn, "delete from accountejb where profile_userid like 'ru:%'");
      int newUserCount = stmt.executeUpdate();
      runStatsData.setNewUserCount(newUserCount);
      stmt.close();

      // Count of trade users
      stmt = getStatement(conn, "select count(accountid) as \"tradeUserCount\" from accountejb a where a.profile_userid like 'uid:%'");
      rs = stmt.executeQuery();
      rs.next();
      int tradeUserCount = rs.getInt("tradeUserCount");
      runStatsData.setTradeUserCount(tradeUserCount);
      stmt.close();

      rs.close();
      // Count of trade stocks
      stmt = getStatement(conn, "select count(symbol) as \"tradeStockCount\" from quoteejb a where a.symbol like 's:%'");
      rs = stmt.executeQuery();
      rs.next();
      int tradeStockCount = rs.getInt("tradeStockCount");
      runStatsData.setTradeStockCount(tradeStockCount);
      stmt.close();

      // Count of trade users login, logout
      stmt = getStatement(conn,
          "select sum(loginCount) as \"sumLoginCount\", sum(logoutCount) as \"sumLogoutCount\" from accountejb a where  a.profile_userID like 'uid:%'");
      rs = stmt.executeQuery();
      rs.next();
      int sumLoginCount = rs.getInt("sumLoginCount");
      int sumLogoutCount = rs.getInt("sumLogoutCount");
      runStatsData.setSumLoginCount(sumLoginCount);
      runStatsData.setSumLogoutCount(sumLogoutCount);
      stmt.close();

      rs.close();
      // Update logoutcount and loginCount back to zero

      stmt = getStatement(conn, "update accountejb set logoutCount=0,loginCount=0 where profile_userID like 'uid:%'");
      stmt.executeUpdate();
      stmt.close();

      // count holdings for trade users
      stmt = getStatement(conn, "select count(holdingid) as \"holdingCount\" from holdingejb h where h.account_accountid in "
          + "(select accountid from accountejb a where a.profile_userid like 'uid:%')");

      rs = stmt.executeQuery();
      rs.next();
      int holdingCount = rs.getInt("holdingCount");
      runStatsData.setHoldingCount(holdingCount);
      stmt.close();
      rs.close();

      // count orders for trade users
      stmt = getStatement(conn, "select count(orderid) as \"orderCount\" from orderejb o where o.account_accountid in "
          + "(select accountid from accountejb a where a.profile_userid like 'uid:%')");

      rs = stmt.executeQuery();
      rs.next();
      int orderCount = rs.getInt("orderCount");
      runStatsData.setOrderCount(orderCount);
      stmt.close();
      rs.close();

      // count orders by type for trade users
      stmt = getStatement(conn, "select count(orderid) \"buyOrderCount\"from orderejb o where (o.account_accountid in "
          + "(select accountid from accountejb a where a.profile_userid like 'uid:%')) AND " + " (o.orderType='buy')");

      rs = stmt.executeQuery();
      rs.next();
      int buyOrderCount = rs.getInt("buyOrderCount");
      runStatsData.setBuyOrderCount(buyOrderCount);
      stmt.close();
      rs.close();

      // count orders by type for trade users
      stmt = getStatement(conn, "select count(orderid) \"sellOrderCount\"from orderejb o where (o.account_accountid in "
          + "(select accountid from accountejb a where a.profile_userid like 'uid:%')) AND " + " (o.orderType='sell')");

      rs = stmt.executeQuery();
      rs.next();
      int sellOrderCount = rs.getInt("sellOrderCount");
      runStatsData.setSellOrderCount(sellOrderCount);
      stmt.close();
      rs.close();

      // Delete cancelled orders
      stmt = getStatement(conn, "delete from orderejb where orderStatus='cancelled'");
      int cancelledOrderCount = stmt.executeUpdate();
      runStatsData.setCancelledOrderCount(cancelledOrderCount);
      stmt.close();
      rs.close();

      // count open orders by type for trade users
      stmt = getStatement(conn, "select count(orderid) \"openOrderCount\"from orderejb o where (o.account_accountid in "
          + "(select accountid from accountejb a where a.profile_userid like 'uid:%')) AND " + " (o.orderStatus='open')");

      rs = stmt.executeQuery();
      rs.next();
      int openOrderCount = rs.getInt("openOrderCount");
      runStatsData.setOpenOrderCount(openOrderCount);

      stmt.close();
      rs.close();
      // Delete orders for holding which have been purchased and sold
      stmt = getStatement(conn, "delete from orderejb where holding_holdingid is null");
      int deletedOrderCount = stmt.executeUpdate();
      runStatsData.setDeletedOrderCount(deletedOrderCount);
      stmt.close();
      rs.close();

      conn.commit();

      System.out.println("TradeDirect:reset Run stats data\n\n" + runStatsData);
    } catch (Exception e) {
      Log.error(e, "Failed to reset Trade");
      conn.rollback();
      throw e;
    } finally {
      conn.close();
    }
    return runStatsData;

  }

  private PreparedStatement getStatement(Connection conn, String sql) throws Exception {
    return conn.prepareStatement(sql);
  }

  public Object[] parseDDLToBuffer(InputStream ddlFile) throws Exception {
    BufferedReader br = null;
    ArrayList<String> sqlBuffer = new ArrayList<String>(30); //initial capacity 30 assuming we have 30 ddl-sql statements to read

    try {
      br = new BufferedReader(new InputStreamReader(ddlFile));
      String s;
      String sql = new String();
      while ((s = br.readLine()) != null) {
        s = s.trim();
        if ((s.length() != 0) && (s.charAt(0) != '#')) // Empty lines or lines starting with "#" are ignored
        {
          sql = sql + " " + s;
          if (s.endsWith(";")) { // reached end of sql statement
            sql = sql.replace(';', ' '); //remove the semicolon
            sqlBuffer.add(sql);
            sql = "";
          }
        }
      }
    } catch (IOException ex) {
      Log.error("TradeBuildDB:parseDDLToBuffer Exeception during open/read of File: " + ddlFile, ex);
      throw ex;
    } finally {
      if (br != null) {
        try {
          br.close();
        } catch (IOException ex) {
          Log.error("TradeBuildDB:parseDDLToBuffer Failed to close BufferedReader", ex);
        }
      }
    }
    return sqlBuffer.toArray();
  }
}
