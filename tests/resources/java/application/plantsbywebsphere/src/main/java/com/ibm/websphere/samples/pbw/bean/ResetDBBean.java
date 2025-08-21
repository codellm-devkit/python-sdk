//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2004,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.bean;

import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.Serializable;
import java.net.URL;
import java.util.Vector;

import javax.annotation.Resource;
import javax.annotation.security.RolesAllowed;
import javax.enterprise.context.Dependent;
import javax.inject.Inject;
import javax.inject.Named;
import javax.persistence.EntityManager;
import javax.persistence.PersistenceContext;
import javax.persistence.PersistenceContextType;
import javax.persistence.Query;
import javax.persistence.SynchronizationType;
import javax.transaction.HeuristicMixedException;
import javax.transaction.HeuristicRollbackException;
import javax.transaction.NotSupportedException;
import javax.transaction.RollbackException;
import javax.transaction.SystemException;
import javax.transaction.Transactional;
import javax.transaction.UserTransaction;

import com.ibm.websphere.samples.pbw.jpa.Inventory;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * ResetDBBean provides a transactional and secure facade to reset all the database information for
 * the PlantsByWebSphere application.
 */

@Named(value = "resetbean")
@Dependent
@RolesAllowed("SampAdmin")
public class ResetDBBean implements Serializable {

	@Inject
	private CatalogMgr catalog;
	@Inject
	private CustomerMgr customer;
	@Inject
	private ShoppingCartBean cart;
	@Inject
	private BackOrderMgr backOrderStock;
	@Inject
	private SuppliersBean suppliers;

	@PersistenceContext(unitName = "PBW")
	EntityManager em;
	
	@Resource
	UserTransaction tx;
	
	public void resetDB() {
		deleteAll();
		populateDB();
	}

	/**
	 * @param itemID
	 * @param fileName
	 * @param catalog
	 * @throws FileNotFoundException
	 * @throws IOException
	 */
	public static void addImage(String itemID,
			String fileName,
			CatalogMgr catalog) throws FileNotFoundException, IOException {
		URL url = Thread.currentThread().getContextClassLoader().getResource("resources/images/" + fileName);
		Util.debug("URL: " + url);
		fileName = url.getPath();
		Util.debug("Fully-qualified Filename: " + fileName);
		File imgFile = new File(fileName);
		// Open the input file as a stream of bytes
		FileInputStream fis = new FileInputStream(imgFile);
		DataInputStream dis = new DataInputStream(fis);
		int dataSize = dis.available();
		byte[] data = new byte[dataSize];
		dis.readFully(data);
		catalog.setItemImageBytes(itemID, data);
	}

	public void populateDB() {
		/**
		 * Populate INVENTORY table with text
		 */
		Util.debug("Populating INVENTORY table with text...");
		try {
			String[] values = Util.getProperties("inventory");
			for (int index = 0; index < values.length; index++) {
				Util.debug("Found INVENTORY property values:  " + values[index]);
				String[] fields = Util.readTokens(values[index], "|");
				String id = fields[0];
				String name = fields[1];
				String heading = fields[2];
				String descr = fields[3];
				String pkginfo = fields[4];
				String image = fields[5];
				float price = new Float(fields[6]).floatValue();
				float cost = new Float(fields[7]).floatValue();
				int quantity = new Integer(fields[8]).intValue();
				int category = new Integer(fields[9]).intValue();
				String notes = fields[10];
				boolean isPublic = new Boolean(fields[11]).booleanValue();
				Util.debug("Populating INVENTORY with following values:  ");
				Util.debug(fields[0]);
				Util.debug(fields[1]);
				Util.debug(fields[2]);
				Util.debug(fields[3]);
				Util.debug(fields[4]);
				Util.debug(fields[5]);
				Util.debug(fields[6]);
				Util.debug(fields[7]);
				Util.debug(fields[8]);
				Util.debug(fields[9]);
				Util.debug(fields[10]);
				Util.debug(fields[11]);
				Inventory storeItem = new Inventory(id, name, heading, descr, pkginfo, image, price, cost, quantity,
						category, notes, isPublic);
				catalog.addItem(storeItem);
				addImage(id, image, catalog);
			}
			Util.debug("INVENTORY table populated with text...");
		} catch (Exception e) {
			Util.debug("Unable to populate INVENTORY table with text data: " + e);
			e.printStackTrace();
		}
		/**
		 * Populate CUSTOMER table with text
		 */
		Util.debug("Populating CUSTOMER table with default values...");
		try {
			String[] values = Util.getProperties("customer");
			Util.debug("Found CUSTOMER properties:  " + values[0]);
			for (int index = 0; index < values.length; index++) {
				String[] fields = Util.readTokens(values[index], "|");
				String customerID = fields[0];
				String password = fields[1];
				String firstName = fields[2];
				String lastName = fields[3];
				String addr1 = fields[4];
				String addr2 = fields[5];
				String addrCity = fields[6];
				String addrState = fields[7];
				String addrZip = fields[8];
				String phone = fields[9];
				Util.debug("Populating CUSTOMER with following values:  ");
				Util.debug(fields[0]);
				Util.debug(fields[1]);
				Util.debug(fields[2]);
				Util.debug(fields[3]);
				Util.debug(fields[4]);
				Util.debug(fields[5]);
				Util.debug(fields[6]);
				Util.debug(fields[7]);
				Util.debug(fields[8]);
				Util.debug(fields[9]);
				customer.createCustomer(customerID, password, firstName, lastName, addr1, addr2, addrCity, addrState, addrZip, phone);
			}
		} catch (Exception e) {
			Util.debug("Unable to populate CUSTOMER table with text data: " + e);
			e.printStackTrace();
		}
		/**
		 * Populate ORDER table with text
		 */
		Util.debug("Populating ORDER table with default values...");
		try {
			String[] values = Util.getProperties("order");
			Util.debug("Found ORDER properties:  " + values[0]);
			if (values[0] != null && values.length > 0) {
				for (int index = 0; index < values.length; index++) {
					String[] fields = Util.readTokens(values[index], "|");
					if (fields != null && fields.length >= 21) {
						String customerID = fields[0];
						String billName = fields[1];
						String billAddr1 = fields[2];
						String billAddr2 = fields[3];
						String billCity = fields[4];
						String billState = fields[5];
						String billZip = fields[6];
						String billPhone = fields[7];
						String shipName = fields[8];
						String shipAddr1 = fields[9];
						String shipAddr2 = fields[10];
						String shipCity = fields[11];
						String shipState = fields[12];
						String shipZip = fields[13];
						String shipPhone = fields[14];
						int shippingMethod = Integer.parseInt(fields[15]);
						String creditCard = fields[16];
						String ccNum = fields[17];
						String ccExpireMonth = fields[18];
						String ccExpireYear = fields[19];
						String cardHolder = fields[20];
						Vector<Inventory> items = new Vector<Inventory>();
						Util.debug("Populating ORDER with following values:  ");
						Util.debug(fields[0]);
						Util.debug(fields[1]);
						Util.debug(fields[2]);
						Util.debug(fields[3]);
						Util.debug(fields[4]);
						Util.debug(fields[5]);
						Util.debug(fields[6]);
						Util.debug(fields[7]);
						Util.debug(fields[8]);
						Util.debug(fields[9]);
						Util.debug(fields[10]);
						Util.debug(fields[11]);
						Util.debug(fields[12]);
						Util.debug(fields[13]);
						Util.debug(fields[14]);
						Util.debug(fields[15]);
						Util.debug(fields[16]);
						Util.debug(fields[17]);
						Util.debug(fields[18]);
						Util.debug(fields[19]);
						Util.debug(fields[20]);
						cart.createOrder(customerID, billName, billAddr1, billAddr2, billCity, billState, billZip, billPhone, shipName, shipAddr1, shipAddr2, shipCity, shipState, shipZip, shipPhone, creditCard, ccNum, ccExpireMonth, ccExpireYear, cardHolder, shippingMethod, items);
					} else {
						Util.debug("Property does not contain enough fields: " + values[index]);
						Util.debug("Fields found were: " + fields);
					}
				}
			}
			// stmt.executeUpdate(" INSERT INTO ORDERITEM(INVENTORYID, NAME, PKGINFO, PRICE, COST,
			// CATEGORY, QUANTITY, SELLDATE, ORDER_ORDERID) VALUES ('A0001', 'Bulb Digger',
			// 'Assembled', 12.0, 5.0, 3, 900, '01054835419625', '1')");
		} catch (Exception e) {
			Util.debug("Unable to populate ORDERITEM table with text data: " + e);
			e.printStackTrace();
			e.printStackTrace();
		}
		/**
		 * Populate BACKORDER table with text
		 */
		Util.debug("Populating BACKORDER table with default values...");
		try {
			String[] values = Util.getProperties("backorder");
			Util.debug("Found BACKORDER properties:  " + values[0]);
			// Inserting backorders
			for (int index = 0; index < values.length; index++) {
				String[] fields = Util.readTokens(values[index], "|");
				String inventoryID = fields[0];
				int amountToOrder = new Integer(fields[1]).intValue();
				int maximumItems = new Integer(fields[2]).intValue();
				Util.debug("Populating BACKORDER with following values:  ");
				Util.debug(inventoryID);
				Util.debug("amountToOrder -> " + amountToOrder);
				Util.debug("maximumItems -> " + maximumItems);
				backOrderStock.createBackOrder(inventoryID, amountToOrder, maximumItems);
			}
		} catch (Exception e) {
			Util.debug("Unable to populate BACKORDER table with text data: " + e);
			e.printStackTrace();
		}
		/**
		 * Populate SUPPLIER table with text
		 */
		Util.debug("Populating SUPPLIER table with default values...");
		try {
			String[] values = Util.getProperties("supplier");
			Util.debug("Found SUPPLIER properties:  " + values[0]);
			// Inserting Suppliers
			for (int index = 0; index < values.length; index++) {
				String[] fields = Util.readTokens(values[index], "|");
				String supplierID = fields[0];
				String name = fields[1];
				String address = fields[2];
				String city = fields[3];
				String state = fields[4];
				String zip = fields[5];
				String phone = fields[6];
				String url = fields[7];
				Util.debug("Populating SUPPLIER with following values:  ");
				Util.debug(fields[0]);
				Util.debug(fields[1]);
				Util.debug(fields[2]);
				Util.debug(fields[3]);
				Util.debug(fields[4]);
				Util.debug(fields[5]);
				Util.debug(fields[6]);
				Util.debug(fields[7]);
				suppliers.createSupplier(supplierID, name, address, city, state, zip, phone, url);
			}
		} catch (Exception e) {
			Util.debug("Unable to populate SUPPLIER table with text data: " + e);
			e.printStackTrace();
		}
	}

	@Transactional
	public void deleteAll() {
		try {
			Query q = em.createNamedQuery("removeAllOrders");
			q.executeUpdate();
			q = em.createNamedQuery("removeAllInventory");
			q.executeUpdate();
			// q=em.createNamedQuery("removeAllIdGenerator");
			// q.executeUpdate();
			q = em.createNamedQuery("removeAllCustomers");
			q.executeUpdate();
			q = em.createNamedQuery("removeAllOrderItem");
			q.executeUpdate();
			q = em.createNamedQuery("removeAllBackOrder");
			q.executeUpdate();
			q = em.createNamedQuery("removeAllSupplier");
			q.executeUpdate();
			em.flush();
			Util.debug("Deleted all data from database");
		} catch (Exception e) {
			Util.debug("ResetDB(deleteAll) -- Error deleting data from the database: " + e);
			e.printStackTrace();
			try {
                tx.setRollbackOnly();
            } catch (IllegalStateException | SystemException ignore) {
            }
		}
	}

}